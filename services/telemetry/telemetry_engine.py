import json
import logging
import os
import re
from typing import Any, Dict, Protocol

# --- CONFIGURATION ---
AUDIT_LOG = "logs/aios_audit.log"
RECOVERY_LOG = "logs/aios_recovery.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MetricResult:
    def __init__(self, name: str, score: float, data: Dict[str, Any]):
        self.name = name
        self.score = score
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            **self.data
        }

class MetricProvider(Protocol):
    def calculate(self, audit_content: str, recovery_content: str) -> MetricResult:
        ...

class IntentMetric:
    def calculate(self, audit_content: str, recovery_content: str) -> MetricResult:
        approved_count = len(re.findall(r"Final Verdict: APPROVED", audit_content))
        rejected_count = len(re.findall(r"Final Verdict: REJECTED", audit_content))
        
        score = 1.0
        if (approved_count + rejected_count) > 0:
            score = approved_count / (approved_count + rejected_count)
            
        return MetricResult("intent_alignment", score, {
            "approved": approved_count,
            "rejected": rejected_count
        })

class StabilityMetric:
    def calculate(self, audit_content: str, recovery_content: str) -> MetricResult:
        critical_errors = len(re.findall(r"CRITICAL: Service '.*' is NOT running", recovery_content))
        successful_restarts = len(re.findall(r"Successfully sent restart command", recovery_content))
        
        score = 1.0
        if critical_errors > 0:
            score = 1.0 / (1.0 + critical_errors)
        elif successful_restarts > 0:
            score = 0.9
            
        return MetricResult("system_stability", score, {
            "critical_errors": critical_errors,
            "successful_restarts": successful_restarts
        })

class ResourceMetric:
    def calculate(self, audit_content: str, recovery_content: str) -> MetricResult:
        disk_warnings = len(re.findall(r"HIGH DISK USAGE: \d+\.\d+%", recovery_content))
        
        score = 1.0
        if disk_warnings > 0:
            score = 1.0 / (1.0 + disk_warnings)
            
        return MetricResult("resource_stability", score, {
            "disk_warnings": disk_warnings
        })

class TelemetryEngine:
    def __init__(self, audit_log_path: str = AUDIT_LOG, recovery_log_path: str = RECOVERY_LOG):
        self.audit_log_path = audit_log_path
        self.recovery_log_path = recovery_log_path
        # List of (provider, weight)
        self.metrics_config = [
            (IntentMetric(), 0.5),
            (StabilityMetric(), 0.3),
            (ResourceMetric(), 0.2)
        ]

    def _parse_log_file(self, file_path: str) -> str:
        if not os.path.exists(file_path):
            return ""
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            return ""

    def calculate_alignment_score(self) -> Dict[str, Any]:
        """
        Calculates a composite alignment score based on multiple metrics.
        Score is between 0.0 and 1.0.
        """
        audit_content = self._parse_log_file(self.audit_log_path)
        recovery_content = self._parse_log_file(self.recovery_log_path)

        results = {}
        composite_score = 0.0

        for provider, weight in self.metrics_config:
            res = provider.calculate(audit_content, recovery_content)
            results[res.name] = res.to_dict()
            composite_score += res.score * weight

        return {
            "composite_score": round(composite_score, 4),
            "metrics": results,
            "timestamp": os.path.getmtime(self.audit_log_path) if os.path.exists(self.audit_log_path) else 0
        }

if __name__ == "__main__":
    # Quick test if logs exist
    engine = TelemetryEngine()
    print(json.dumps(engine.calculate_alignment_score(), indent=2))