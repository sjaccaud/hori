import pytest
from unittest.mock import patch, MagicMock
from services.telemetry.telemetry_engine import TelemetryEngine

@pytest.fixture
def engine():
    # We don't need to mock much here as we can just provide paths to files we control
    # or mock the _parse_log_file method.
    with patch.object(TelemetryEngine, '_parse_log_file') as mock_parse:
        yield mock_parse

def test_telemetry_alignment_score_calculation(engine):
    """
    Regression test for TelemetryEngine alignment score calculation.
    Ensures that the composite score is calculated correctly based on mock log content.
    """
    # Mocking the log content
    # IntentMetric: 1 approved, 0 rejected -> score 1.0
    # StabilityMetric: 0 critical errors, 1 successful restart -> score 0.9
    # ResourceMetric: 0 disk warnings -> score 1.0
    
    # We need to return different content for audit and recovery logs
    # audit_log: contains "Final Verdict: APPROVED"
    # recovery_log: contains "Successfully sent restart command"
    
    def side_effect(path):
        if "audit" in path:
            return "Final Verdict: APPROVED"
        if "recovery" in path:
            return "Successfully sent restart command"
        return ""

    engine.side_effect = side_effect
    
    # Re-instantiate engine with the mocked side effect
    with patch.object(TelemetryEngine, '_parse_log_file', side_effect=side_effect):
        te = TelemetryEngine(audit_log_path="audit.log", recovery_log_path="recovery.log")
        report = te.calculate_alignment_score()
        
        # Expected composite score:
        # Intent (0.5 weight) * 1.0 = 0.5
        # Stability (0.3 weight) * 0.9 = 0.27
        # Resource (0.2 weight) * 1.0 = 0.2
        # Total = 0.5 + 0.27 + 0.2 = 0.97
        
        assert report['composite_score'] == 0.97
        assert report['metrics']['intent_alignment']['score'] == 1.0
        assert report['metrics']['system_stability']['score'] == 0.9
        assert report['metrics']['resource_stability']['score'] == 1.0

def test_telemetry_alignment_score_failure(engine):
    """
    Regression test for TelemetryEngine alignment score calculation.
    Ensures that a low alignment score is correctly reported.
    """
    def side_effect(path):
        if "audit" in path:
            return "Final Verdict: REJECTED"
        if "recovery" in path:
            return "CRITICAL: Service 'test' is NOT running"
        return ""

    with patch.object(TelemetryEngine, '_parse_log_file', side_effect=side_effect):
        te = TelemetryEngine(audit_log_path="audit.log", recovery_log_path="recovery.log")
        report = te.calculate_alignment_score()
        
        # Expected composite score:
        # Intent (0.5 weight) * 0.0 = 0.0
        # Stability (0.3 weight) * (1.0 / (1.0 + 1)) = 0.5 * 0.3 = 0.15
        # Resource (0.2 weight) * 1.0 = 0.2
        # Total = 0.0 + 0.15 + 0.2 = 0.35
        
        assert report['composite_score'] == 0.35
        assert report['metrics']['intent_alignment']['score'] == 0.0
        assert report['metrics']['system_stability']['score'] == 0.5