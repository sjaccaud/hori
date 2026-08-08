import json
import os
from pathlib import Path


class AlignmentEngine:
    def __init__(self, manifesto_path, charter_path, watchdog_log_path):
        self.manifesto_path = Path(manifesto_path)
        self.charter_path = Path(charter_path)
        self.watchdog_log_path = Path(watchdog_log_path)
        self.manifesto = self._load_json(self.manifesto_path)
        self.charter = self._load_json(self.charter_path)

    def _load_json(self, path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {path}: {e}")
            return {}

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
        Checks if the core intent hierarchy is intact.
        """
        score = 0.0
        if self.manifesto and self.charter:
            score += 0.5
        
        # Check if manifesto and charter are linked
        if self.manifesto.get("id") == self.charter.get("parent_manifesto_id"):
            score += 0.5
            
        return score

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
            "manifesto_id": self.manifesto.get("id"),
            "charter_id": self.charter.get("id"),
            "status": "Aligned" if score > 0.8 else "Drifting" if score > 0.5 else "Critical Drift"
        }

if __name__ == "__main__":
    # Test run
    engine = AlignmentEngine(
        "core/intent/manifesto.json",
        "core/intent/charter.json",
        "services/recovery/watchdog.log"
    )
    print(json.dumps(engine.get_report(), indent=2))