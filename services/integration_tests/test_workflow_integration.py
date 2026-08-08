"""
Integration tests for the aios-core chat pipeline.

Tests the end-to-end flow:
1. Send a chat request to aios-core /chat
2. Verify intent parsing, memory retrieval, red-team gate, and response
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.aios_core.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_safe_command_flow(client):
    """
    A safe command should pass through aios-core without red-team intervention.
    """
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._call_llm") as mock_llm, \
         patch("services.aios_core.main._persist_turn") as mock_persist:
        mock_intent.return_value = {
            "type": "work_order", "id": "wo-1",
            "description": "Buy some milk", "status": "backlog", "priority": "low"
        }
        mock_mem.return_value = []
        mock_llm.return_value = "I'll help you with that."

        response = client.post("/chat", json={"text": "Buy some milk"})

        assert response.status_code == 200
        data = response.json()
        assert data["response"] == "I'll help you with that."
        assert data["work_order"]["description"] == "Buy some milk"
        assert data["red_team_report"] is None


def test_destructive_command_triggers_red_team(client):
    """
    A destructive command should trigger the red-team gate and return REJECTED.
    """
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._persist_turn"), \
         patch("services.aios_core.main._run_red_team") as mock_redteam:
        mock_intent.return_value = {
            "type": "work_order", "id": "wo-2",
            "description": "Delete all files", "status": "backlog", "priority": "high"
        }
        mock_mem.return_value = []
        mock_redteam.return_value = {
            "verdict": "REJECTED",
            "alternatives": [],
            "persona_evaluations": {},
        }

        response = client.post("/chat", json={"text": "please delete all files in core"})

        assert response.status_code == 200
        data = response.json()
        assert "REJECTED" in data["response"]
        assert data["red_team_report"]["verdict"] == "REJECTED"


def test_yes_and_flow(client):
    """
    A destructive command with a Yes-AND verdict should return alternatives.
    """
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._persist_turn"), \
         patch("services.aios_core.main._run_red_team") as mock_redteam:
        mock_intent.return_value = {
            "type": "work_order", "id": "wo-3",
            "description": "Delete old logs", "status": "backlog", "priority": "medium"
        }
        mock_mem.return_value = []
        mock_redteam.return_value = {
            "verdict": "YES_AND",
            "alternatives": ["Archive before deleting."],
            "persona_evaluations": {},
        }

        response = client.post("/chat", json={"text": "delete the old log files"})

        assert response.status_code == 200
        data = response.json()
        assert "YES_AND" in data["response"]
        assert "Archive" in data["response"]
        assert len(data["red_team_report"]["alternatives"]) == 1
