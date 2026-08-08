import pytest
from unittest.mock import patch, MagicMock
from services.red_teaming.engine import RedTeamingEngine

@pytest.fixture
def engine():
    with patch.object(RedTeamingEngine, '_load_json') as mock_load:
        mock_load.side_effect = [
            {"personas": {
                "Strategist": {"role": "Strategist", "system_prompt": "You are a strategist."},
                "Architect": {"role": "Architect", "system_prompt": "You are an architect."},
                "Guardian": {"role": "Guardian", "system_prompt": "You are a guardian."}
            }},
            {"key": "value"} # manifesto
        ]
        with patch("builtins.open", MagicMock()):
            yield RedTeamingEngine()

def test_red_teaming_verdict_logic(engine):
    """
    Regression test for RedTeamingEngine verdict logic.
    Ensures that if any persona rejects, the final verdict is REJECTED.
    """
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        # Side effect: first two approve, third one rejects
        mocked_llm.side_effect = [
            "This action is fine. [APPROVED]",
            "Technically feasible. [APPROVED]",
            "This violates safety protocols! [REJECTED]"
        ]
        
        test_action = {
            "type": "work_order",
            "description": "Delete the entire system",
            "priority": "critical"
        }
        
        report = engine.evaluate_action(test_action)
        assert report['verdict'] == "REJECTED"
        assert 'alternatives' in report

def test_red_teaming_all_approve(engine):
    """
    Regression test for RedTeamingEngine verdict logic.
    Ensures that if all personas approve, the final verdict is APPROVED.
    """
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.return_value = "This action is fine. [APPROVED]"
        
        test_action = {
            "type": "work_order",
            "description": "Buy some milk",
            "priority": "low"
        }
        
        report = engine.evaluate_action(test_action)
        assert report['verdict'] == "APPROVED"
        assert report['alternatives'] == []

def test_red_teaming_yes_and_regression(engine):
    """
    Regression test for the Yes, AND mechanism.
    Ensures YES_AND verdicts produce alternatives and don't override REJECTED.
    """
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.side_effect = [
            "Good idea. [APPROVED]",
            "Risky but fixable. [YES_AND] ALTERNATIVE: Add a dry-run step first.",
            "Safe with care. [APPROVED]"
        ]
        
        test_action = {"description": "Batch delete old logs"}
        report = engine.evaluate_action(test_action)
        assert report['verdict'] == "YES_AND"
        assert len(report['alternatives']) == 1
        assert "dry-run" in report['alternatives'][0].lower()