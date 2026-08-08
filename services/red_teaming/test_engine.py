import pytest
from unittest.mock import patch, MagicMock
import requests
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

def test_evaluate_action_all_approve(engine):
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.return_value = "This action is fine. [APPROVED]"
        
        test_action_ok = {
            "type": "work_order",
            "description": "Buy some milk",
            "priority": "low"
        }
        
        report_ok = engine.evaluate_action(test_action_ok)
        assert report_ok['verdict'] == "APPROVED"
        assert len(report_ok['persona_evaluations']) == 3
        assert report_ok['alternatives'] == []

def test_evaluate_action_one_rejects(engine):
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        # Side effect: first two approve, third one rejects
        mocked_llm.side_effect = [
            "Looks good. [APPROVED]",
            "Technically feasible. [APPROVED]",
            "This violates safety protocols! [REJECTED]"
        ]
        
        test_action_bad = {
            "type": "work_order",
            "description": "Delete the entire system",
            "priority": "critical"
        }
        
        report_bad = engine.evaluate_action(test_action_bad)
        assert report_bad['verdict'] == "REJECTED"

def test_evaluate_action_case_insensitivity(engine):
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.return_value = "This is bad. [rejected]" # lowercase
        
        test_action_ok = {"description": "test"}
        report_case = engine.evaluate_action(test_action_ok)
        assert report_case['verdict'] == "REJECTED"

def test_evaluate_action_yes_and(engine):
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.side_effect = [
            "Looks good. [APPROVED]",
            "Feasible but risky. [YES_AND] ALTERNATIVE: Add a backup step before deletion.",
            "Safe with modification. [APPROVED]"
        ]
        
        test_action = {
            "type": "work_order",
            "description": "Delete old log files",
            "priority": "medium"
        }
        
        report = engine.evaluate_action(test_action)
        assert report['verdict'] == "YES_AND"
        assert len(report['alternatives']) == 1
        assert "backup" in report['alternatives'][0].lower()

def test_evaluate_action_rejected_overrides_yes_and(engine):
    with patch.object(RedTeamingEngine, '_call_llm') as mocked_llm:
        mocked_llm.side_effect = [
            "Looks good. [APPROVED]",
            "Risky but fixable. [YES_AND] ALTERNATIVE: Use a sandbox first.",
            "Absolutely not. [REJECTED]"
        ]
        
        test_action = {"description": "rm -rf /"}
        report = engine.evaluate_action(test_action)
        assert report['verdict'] == "REJECTED"
        assert len(report['alternatives']) == 1

def test_call_llm_success(engine):
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Success!"}}]
        }
        mock_post.return_value = mock_response
        
        response = engine._call_llm("system", "user")
        assert response == "Success!"

def test_call_llm_failure(engine):
    with patch('requests.post') as mock_post:
        mock_post.side_effect = Exception("Connection error")
        
        response = engine._call_llm("system", "user")
        assert "ERROR: LLM call failed" in response