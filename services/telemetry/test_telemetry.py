import unittest
from unittest.mock import patch

from telemetry_engine import TelemetryEngine


class TestTelemetryEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TelemetryEngine(audit_log_path="mock_audit.log", recovery_log_path="mock_recovery.log")

    @patch.object(TelemetryEngine, '_parse_log_file')
    def test_calculate_alignment_score_empty_logs(self, mock_parse):
        # Mock both logs as empty
        mock_parse.return_value = ""
        
        result = self.engine.calculate_alignment_score()
        
        # Default scores: intent=1.0, stability=1.0, resource=1.0 -> composite=1.0
        self.assertEqual(result["composite_score"], 1.0)
        self.assertEqual(result["metrics"]["intent_alignment"]["score"], 1.0)
        self.assertEqual(result["metrics"]["system_stability"]["score"], 1.0)
        self.assertEqual(result["metrics"]["resource_stability"]["score"], 1.0)

    @patch.object(TelemetryEngine, '_parse_log_file')
    def test_calculate_alignment_score_mixed_logs(self, mock_parse):
        # Mock audit log with 2 approved, 1 rejected
        # Mock recovery log with 1 critical error and 1 disk warning
        def side_effect(path):
            if path == "mock_audit.log":
                return "Final Verdict: APPROVED\nFinal Verdict: APPROVED\nFinal Verdict: REJECTED"
            if path == "mock_recovery.log":
                return (
                    "🚨 [WATCHDOG] CRITICAL: Service 'test_service' is NOT running\n"
                    "⚠️ [WATCHDOG] HIGH DISK USAGE: 95.5%"
                )
            return ""

        mock_parse.side_effect = side_effect
        
        result = self.engine.calculate_alignment_score()
        
        # Intent score: 2 / (2+1) = 0.6667
        # Stability score: 1 / (1+1) = 0.5
        # Resource score: 1 / (1+1) = 0.5
        # Composite: (0.6666666666666666 * 0.5) + (0.5 * 0.3) + (0.5 * 0.2)
        #           = 0.3333333333333333 + 0.15 + 0.1 = 0.5833333333333333
        # Rounded to 4 decimal places: 0.5833
        
        self.assertAlmostEqual(result["metrics"]["intent_alignment"]["score"], 0.6667, places=4)
        self.assertAlmostEqual(result["metrics"]["system_stability"]["score"], 0.5, places=4)
        self.assertAlmostEqual(result["metrics"]["resource_stability"]["score"], 0.5, places=4)
        self.assertAlmostEqual(result["composite_score"], 0.5833, places=4)

    @patch.object(TelemetryEngine, '_parse_log_file')
    def test_calculate_alignment_score_recovery_success(self, mock_parse):
        # Mock audit log with 1 approved
        # Mock recovery log with 1 successful restart (no critical errors, no disk warnings)
        def side_effect(path):
            if path == "mock_audit.log":
                return "Final Verdict: APPROVED"
            if path == "mock_recovery.log":
                return "✅ [WATCHDOG] Successfully sent restart command to test_service"
            return ""

        mock_parse.side_effect = side_effect
        
        result = self.engine.calculate_alignment_score()
        
        # Intent score: 1/1 = 1.0
        # Stability score: 0.9 (since successful_restarts > 0 and no critical errors)
        # Resource score: 1.0 (no disk warnings)
        # Composite: (1.0 * 0.5) + (0.9 * 0.3) + (1.0 * 0.2)
        #           = 0.5 + 0.27 + 0.2 = 0.97
        
        self.assertEqual(result["metrics"]["intent_alignment"]["score"], 1.0)
        self.assertEqual(result["metrics"]["system_stability"]["score"], 0.9)
        self.assertEqual(result["metrics"]["resource_stability"]["score"], 1.0)
        self.assertAlmostEqual(result["composite_score"], 0.97, places=4)

if __name__ == "__main__":
    unittest.main()