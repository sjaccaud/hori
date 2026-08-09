import json
import os
from pathlib import Path


class AlignmentEngine:
    """Calculates an alignment score from 0.0 to 1.0 based on heuristics.

    The manifesto is now read as markdown text (docs/manifesto.md) instead
    of structured JSON. The charter JSON was removed — the alignment engine
    is standalone code, not running in production, so the charter linkage
    check is simplified to just verifying the manifesto exists.
    """

    def __init__(self, manifesto_path, watchdog_log_path):
        self.manifesto_path = Path(manifesto_path)
        self.watchdog_log_path = Path(watchdog_log_path)
        self.manifesto = self._load_text(self.manifesto_path)

    def _load_text(self, path):
        try:
            with open(path, 'r') as f:
                return f.read()
        except Exception as e:
            print(f"Error loading {path}: {e}")
            return ""

    def calculate_score(self):
        """
        Calculates an alignment score from 0.0 to 1.0 based on heuristics.
        """
        checks = [
            self._check_intent_integrity(),
            self._check_system_stability(),
            self._check_governance_compliance()
        ]

        if not checks:
            return 0.0

        return sum(checks) / len(checks)

    def _check_intent_integrity(self):
        """
        Checks if the manifesto is present and non-empty.
        """
        if self.manifesto and len(self.manifesto) > 100:
            return 1.0
        return 0.0

    def _check_system_stability(self):
        """
        Checks for system entropy by scanning watchdog logs for failures.
        """
        if not self.watchdog_log_path.exists():
            return 1.0 # Assume stable if no logs exist yet

        try:
            with open(self.watchdog_log_path, 'r') as f:
                logs = f.readlines()

            # Count failures/warnings in recent logs
            failures = sum(1 for line in logs if "DOWN" in line or "Failed" in line)

            # If failures are low, high score. If high, low score.
            if failures == 0:
                return 1.0
            elif failures < 3:
                return 0.7
            else:
                return 0.3
        except Exception:
            return 0.5

    def _check_governance_compliance(self):
        """
        Heuristic check for governance compliance (e.g., presence of audit logs).
        """
        # For PoC, we check if the watchdog log is being updated
        if self.watchdog_log_path.exists():
            mtime = os.path.getmtime(self.watchdog_log_path)
            import time
            if time.time() - mtime < 300: # Updated in last 5 mins
                return 1.0
        return 0.0

    def get_report(self):
        score = self.calculate_score()
        return {
            "alignment_score": round(score, 2),
            "manifesto_present": bool(self.manifesto),
            "status": (
                "Aligned" if score > 0.8
                else "Drifting" if score > 0.5
                else "Critical Drift"
            ),
        }

if __name__ == "__main__":
    # Test run
    engine = AlignmentEngine(
        "docs/manifesto.md",
        "services/recovery/watchdog.log"
    )
    print(json.dumps(engine.get_report(), indent=2))
