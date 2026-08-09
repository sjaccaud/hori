import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.aios_core.main import (
    OAIMessage,
    OAIChatRequest,
    _elastic_context,
    _is_deflection,
    _is_vague_reference,
    _trim_conversation,
    app,
)
from services.aios_core.state import build_context_block


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_client():
    """Client with AIOS_ADMIN_TOKEN set for authenticated admin API calls."""
    old = os.environ.get("AIOS_ADMIN_TOKEN")
    os.environ["AIOS_ADMIN_TOKEN"] = "test-admin-token"
    yield TestClient(app)
    if old is not None:
        os.environ["AIOS_ADMIN_TOKEN"] = old
    else:
        os.environ.pop("AIOS_ADMIN_TOKEN", None)


def _admin_headers():
    return {"Authorization": "Bearer test-admin-token"}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_non_destructive(client):
    """A normal chat message should go through the full pipeline without red-team."""
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._call_llm") as mock_llm, \
         patch("services.aios_core.main._persist_turn") as mock_persist:
        mock_intent.return_value = {"type": "work_order", "id": "wo-1", "description": "test"}
        mock_mem.return_value = [{"role": "user", "content": "prior context"}]
        mock_llm.return_value = "Here's a helpful response."

        response = client.post("/chat", json={"text": "Help me write a function"})

        assert response.status_code == 200
        data = response.json()
        assert data["response"] == "Here's a helpful response."
        assert data["work_order"]["id"] == "wo-1"
        assert data["red_team_report"] is None
        assert len(data["memory_used"]) > 0
        assert data["conversation_id"]
        mock_persist.assert_called_once()


def test_chat_destructive_rejected(client):
    """A destructive message should trigger red-team and return REJECTED."""
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._run_red_team") as mock_redteam, \
         patch("services.aios_core.main._persist_turn") as mock_persist:
        mock_intent.return_value = {"type": "work_order", "id": "wo-2", "description": "rm -rf"}
        mock_mem.return_value = []
        mock_redteam.return_value = {
            "verdict": "REJECTED",
            "alternatives": [],
            "persona_evaluations": {},
        }

        response = client.post("/chat", json={"text": "please rm -rf the entire project"})

        assert response.status_code == 200
        data = response.json()
        assert "REJECTED" in data["response"]
        assert data["red_team_report"]["verdict"] == "REJECTED"
        mock_persist.assert_called_once()


def test_chat_destructive_yes_and(client):
    """A destructive message with a Yes-AND verdict should return alternatives."""
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._run_red_team") as mock_redteam, \
         patch("services.aios_core.main._persist_turn") as mock_persist:
        mock_intent.return_value = {"type": "work_order", "id": "wo-3", "description": "delete logs"}
        mock_mem.return_value = []
        mock_redteam.return_value = {
            "verdict": "YES_AND",
            "alternatives": ["Add a backup step before deletion."],
            "persona_evaluations": {},
        }

        response = client.post("/chat", json={"text": "delete all the old log files"})

        assert response.status_code == 200
        data = response.json()
        assert "YES_AND" in data["response"]
        assert "backup" in data["response"].lower()
        assert len(data["red_team_report"]["alternatives"]) == 1


def test_chat_assigns_conversation_id(client):
    """If no conversation_id is provided, one should be generated."""
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._call_llm") as mock_llm, \
         patch("services.aios_core.main._persist_turn"):
        mock_intent.return_value = None
        mock_mem.return_value = []
        mock_llm.return_value = "Response"

        response = client.post("/chat", json={"text": "hello"})

        data = response.json()
        assert data["conversation_id"]
        assert len(data["conversation_id"]) > 10  # UUID


def test_state_context_block():
    """build_context_block should return a string with state info."""
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct",
            "active_interests": ["AI", "music"],
            "tone_tags": ["concise"],
            "skills": ["python"],
        }
        mock_project.return_value = {
            "active_projects": [
                {"name": "AIOS", "status": "in_progress", "last_touched": "2026-08-03",
                 "open_questions": ["How to wire voice?"], "next_actions": ["Build aios-core"]}
            ],
            "recent_decisions": ["Use llama.cpp over Ollama"],
        }

        block = build_context_block()

        assert "USER CONTEXT" in block
        assert "direct" in block
        assert "AI" in block
        assert "PROJECT STATE" in block
        assert "AIOS" in block
        assert "How to wire voice?" in block


def test_state_context_block_caps_interests():
    """build_context_block must cap active_interests to MAX_INTERESTS to
    prevent context-window bloat. The 500-turn stress run showed 209
    near-duplicate interests consuming ~13K tokens of the 16K context
    window, causing generation truncation at turns 300 and 500."""
    from services.aios_core.state import MAX_INTERESTS

    # 50 interests — well over the cap
    interests = [f"topic_{i}" for i in range(50)]
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct",
            "active_interests": interests,
            "tone_tags": [],
            "skills": [],
        }
        mock_project.return_value = {"active_projects": [], "recent_decisions": []}

        block = build_context_block()

        # Count how many interests appear in the block
        interest_line = [l for l in block.split("\n") if l.startswith("Active interests:")][0]
        listed = [i.strip() for i in interest_line[len("Active interests: "):].split(",")]
        assert len(listed) <= MAX_INTERESTS, (
            f"Expected at most {MAX_INTERESTS} interests, got {len(listed)}"
        )


def test_state_context_block_dedupes_near_duplicate_interests():
    """build_context_block must deduplicate near-duplicate interests.
    The real user_model.json had 'Safety Spine', 'Safety Spine Implementation',
    'Safety Spine Architecture', 'Safety Gates', 'Safety Gates & Red-Teaming'
    — all referring to the same thing. These should be collapsed."""
    interests = [
        "Safety Spine",
        "Safety Spine Implementation",
        "Safety Spine Architecture",
        "Safety Gates",
        "Safety Gates & Red-Teaming",
        "Qdrant Memory Tiering",
        "Qdrant Tiers",
        "Qdrant Architecture",
        "Local AI",
        "Local-First AI",
    ]
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct",
            "active_interests": interests,
            "tone_tags": [],
            "skills": [],
        }
        mock_project.return_value = {"active_projects": [], "recent_decisions": []}

        block = build_context_block()

        interest_line = [l for l in block.split("\n") if l.startswith("Active interests:")][0]
        listed = [i.strip() for i in interest_line[len("Active interests: "):].split(",")]
        # 10 inputs with 3 near-dup groups → at most 7 unique
        assert len(listed) <= 7, (
            f"Expected near-duplicates to be collapsed, got {len(listed)}: {listed}"
        )


def test_state_context_block_caps_open_questions():
    """build_context_block must cap open_questions per project to
    MAX_QUESTIONS_PER_PROJECT. The real project_state.json had 571
    open questions across 2 projects — most stale."""
    from services.aios_core.state import MAX_QUESTIONS_PER_PROJECT

    questions = [f"Question number {i}?" for i in range(100)]
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct",
            "active_interests": [],
            "tone_tags": [],
            "skills": [],
        }
        mock_project.return_value = {
            "active_projects": [
                {"name": "AIOS", "status": "in_progress", "last_touched": "2026-08-07",
                 "open_questions": questions, "next_actions": []}
            ],
            "recent_decisions": [],
        }

        block = build_context_block()

        q_lines = [l for l in block.split("\n") if l.strip().startswith("? ")]
        assert len(q_lines) <= MAX_QUESTIONS_PER_PROJECT, (
            f"Expected at most {MAX_QUESTIONS_PER_PROJECT} questions, got {len(q_lines)}"
        )


def test_state_context_block_total_char_budget():
    """build_context_block total output must stay under MAX_CONTEXT_CHARS.
    With 209 interests and 571 questions, the block was 53K chars (~13K tokens),
    consuming 82% of the 16K context window. This must not happen again."""
    from services.aios_core.state import MAX_CONTEXT_CHARS

    # Simulate the worst case: huge interests + huge questions
    interests = [f"Very Long Topic Name Number {i}" for i in range(200)]
    questions = [f"What is the detailed plan for component {i}?" for i in range(200)]
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct, concise, no fluff, very detailed",
            "active_interests": interests,
            "tone_tags": ["concise", "direct", "technical", "formal"],
            "skills": ["python", "rust", "go", "javascript", "sql", "docker", "kubernetes"],
        }
        mock_project.return_value = {
            "active_projects": [
                {"name": "AIOS", "status": "in_progress", "last_touched": "2026-08-07",
                 "open_questions": questions, "next_actions": ["Do thing 1", "Do thing 2"]},
                {"name": "Project2", "status": "planning", "last_touched": "2026-08-01",
                 "open_questions": questions, "next_actions": []},
            ],
            "recent_decisions": [f"Decision {i}" for i in range(20)],
        }

        block = build_context_block()

        assert len(block) <= MAX_CONTEXT_CHARS, (
            f"Context block is {len(block)} chars, exceeds budget of {MAX_CONTEXT_CHARS}"
        )


def test_state_context_block_preserves_important_info():
    """Capping must not strip essential fields: communication style, project
    names, statuses, and recent decisions must always appear."""
    with patch("services.aios_core.state.load_user_model") as mock_user, \
         patch("services.aios_core.state.load_project_state") as mock_project:
        mock_user.return_value = {
            "communication_style": "direct",
            "active_interests": ["AI"],
            "tone_tags": ["concise"],
            "skills": ["python"],
        }
        mock_project.return_value = {
            "active_projects": [
                {"name": "AIOS", "status": "in_progress", "last_touched": "2026-08-03",
                 "open_questions": ["How to wire voice?"], "next_actions": ["Build aios-core"]}
            ],
            "recent_decisions": ["Use llama.cpp over Ollama"],
        }

        block = build_context_block()

        assert "direct" in block
        assert "AIOS" in block
        assert "in_progress" in block
        assert "How to wire voice?" in block
        assert "Use llama.cpp over Ollama" in block


def test_system_state_endpoint(client):
    """System state snapshot should return structured status info."""
    with patch("services.aios_core.main.subprocess") as mock_subprocess, \
         patch("services.aios_core.main.httpx.get") as mock_httpx, \
         patch("services.aios_core.main.shutil.disk_usage") as mock_disk:
        # Mock rocm-smi for VRAM
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="GPU[0]\t\t: VRAM Total Memory (B): 34208743424\nGPU[0]\t\t: VRAM Total Used Memory (B): 16152805376\n",
        )
        # Mock model list
        mock_httpx.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "Qwen3.6-27B"}]},
        )
        # Mock disk
        mock_disk.return_value = MagicMock(total=1.8e12, used=3.4e11, free=1.4e12)

        response = client.get("/system/state")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data
        assert "vram" in data
        assert "disk" in data
        assert "loaded_model" in data
        assert data["loaded_model"] == "Qwen3.6-27B"


def test_incident_reporting(client):
    """Test incident report and retrieval endpoints."""
    # Clear any existing incidents
    from services.aios_core.main import _save_incidents
    _save_incidents([])

    # Report an incident
    response = client.post("/system/incident", json={
        "service": "test-service",
        "incident_type": "service_down",
        "severity": "critical",
        "description": "Test incident for unit test",
        "action_taken": "restart attempted",
        "resolved": False,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged"
    assert "incident_id" in data

    # Retrieve incidents
    response = client.get("/system/incidents")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any(i["service"] == "test-service" for i in data["incidents"])

    # Resolve the incident
    incident_id = data["incidents"][-1]["id"]
    response = client.post(f"/system/incidents/{incident_id}/resolve")
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    # Verify it's resolved
    response = client.get("/system/incidents?unresolved_only=true")
    data = response.json()
    assert not any(i["id"] == incident_id for i in data["incidents"])

    # Clean up
    _save_incidents([])


# --- Admin Panel Tests ---

def test_admin_page_served(client):
    """The /admin route should serve the admin HTML page."""
    response = client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "HORI Admin" in response.text


def test_admin_api_state(admin_client):
    """The /admin/api/state endpoint should return system state."""
    with patch("services.aios_core.main.subprocess") as mock_subprocess, \
         patch("services.aios_core.main.httpx.get") as mock_httpx, \
         patch("services.aios_core.main.shutil.disk_usage") as mock_disk:
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="GPU[0]\t\t: VRAM Total Memory (B): 34208743424\nGPU[0]\t\t: VRAM Total Used Memory (B): 16152805376\n",
        )
        mock_httpx.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "Qwen3.6-27B"}]},
        )
        mock_disk.return_value = MagicMock(total=1.8e12, used=3.4e11, free=1.4e12)

        response = admin_client.get("/admin/api/state", headers=_admin_headers())
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "vram" in data


def test_admin_api_gate(admin_client):
    """The /admin/api/gate endpoint should return gate criteria metrics."""
    response = admin_client.get("/admin/api/gate", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert "sherpa_level_3plus" in data
    assert "sherpa_level_2" in data
    assert "hallucination_interceptions" in data
    assert "tool_calls_total" in data


def test_admin_api_service_control_allowlist(admin_client):
    """Service control should reject services not in the allowlist."""
    response = admin_client.post("/admin/api/service/restart?service=evil_service", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "not in allowlist" in data["error"]


def test_admin_api_service_control_bad_action(admin_client):
    """Service control should reject actions other than restart/status."""
    response = admin_client.post("/admin/api/service/hack?service=aios_core", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "not supported" in data["error"]


def test_admin_api_service_self_restart_detached(admin_client):
    """Self-restart (aios_core) should return immediately with a deferred message,
    not block waiting for the restart to complete."""
    with patch("subprocess.Popen") as mock_popen:
        response = admin_client.post("/admin/api/service/restart?service=aios_core", headers=_admin_headers())
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "1.5s" in data["output"]
        # Popen should have been called to spawn the detached restart
        mock_popen.assert_called_once()


def test_admin_api_logs_unknown_type(admin_client):
    """Log viewer should reject unknown log types."""
    response = admin_client.get("/admin/api/logs/nonexistent", headers=_admin_headers())
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "Unknown log type" in data["error"]


def test_chat_strips_tool_call_artifacts(client):
    """When the LLM emits a response with tool-call artifacts that the
    parser doesn't recognize as a real tool call (e.g. embedded in prose),
    the artifacts should be stripped before returning to the user.

    This tests the strip_tool_call_artifacts safety net wired into the
    /chat endpoint. Defends: PoC 16.1 safety net.
    """
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._call_llm") as mock_llm, \
         patch("services.aios_core.main._persist_turn"), \
         patch("services.aios_core.main.get_tool_client") as mock_get_client:
        mock_intent.return_value = {"type": "chat", "id": None, "description": "test"}
        mock_mem.return_value = []
        # LLM emits a response with a leaked [tool_call ...] block that
        # the parser doesn't catch as a real tool call (it's in prose)
        mock_llm.return_value = (
            'Let me check that.\n'
            '[tool_call {"name": "count_files", "path": "/tmp"}]\n'
            'I found the answer.'
        )
        # Tools not available — so _maybe_call_tool is skipped, but
        # strip_tool_call_artifacts should still clean the response
        mock_client = MagicMock()
        mock_client.is_available.return_value = False
        mock_get_client.return_value = mock_client

        response = client.post("/chat", json={"text": "How many files?"})

        assert response.status_code == 200
        data = response.json()
        assert "tool_call" not in data["response"]
        assert "count_files" not in data["response"]
        assert "Let me check that." in data["response"]
        assert "I found the answer." in data["response"]


def test_chat_strips_bare_json_artifacts(client):
    """Bare {"name": "...", "args": {...}} JSON that leaks into a response
    should be stripped by the safety net."""
    with patch("services.aios_core.main.parse_intent") as mock_intent, \
         patch("services.aios_core.main.retrieve_memory") as mock_mem, \
         patch("services.aios_core.main._call_llm") as mock_llm, \
         patch("services.aios_core.main._persist_turn"), \
         patch("services.aios_core.main.get_tool_client") as mock_get_client:
        mock_intent.return_value = {"type": "chat", "id": None, "description": "test"}
        mock_mem.return_value = []
        mock_llm.return_value = (
            'Here is your answer. {"name": "list_dir", "args": {"path": "/home"}}'
        )
        mock_client = MagicMock()
        mock_client.is_available.return_value = False
        mock_get_client.return_value = mock_client

        response = client.post("/chat", json={"text": "List my files"})

        assert response.status_code == 200
        data = response.json()
        assert "list_dir" not in data["response"]
        assert "Here is your answer." in data["response"]


# --- Elastic Context Window (docs/operations.md) ---


def _msgs(*pairs):
    """Helper: build a list of OAIMessage from (role, content) tuples."""
    return [OAIMessage(role=r, content=c) for r, c in pairs]


def test_elastic_context_retrieves_relevant_turns():
    """When relevant older turns exist in Qdrant, _elastic_context pulls
    them in and places them (chronologically) before the recent window.

    This is the core of the elastic window: a context_reference prompt
    at turn 440 retrieves turn 105 where the topic was actually
    discussed. Defends: docs/operations.md, PoC 13.1.
    """
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI operating system."),
        ("user", "Tell me about the architecture."),
        ("assistant", "It uses a three-tier Qdrant memory."),
        ("user", "Remember that architecture we discussed earlier?"),
    )
    user_text = "Remember that architecture we discussed earlier?"
    conv_id = "conv-elastic-1"

    # Qdrant returns an older turn about architecture (simulating turn 105).
    fake_hits = [
        {"role": "user", "content": "Let's design the architecture for AIOS.",
         "conversation_id": conv_id, "timestamp": "t-105"},
        {"role": "assistant", "content": "We'll use a three-tier memory with Qdrant.",
         "conversation_id": conv_id, "timestamp": "t-106"},
    ]

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=fake_hits) as mock_retrieve:
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    # retrieve_conversation_turns was called with the prompt + conv_id.
    mock_retrieve.assert_called_once()
    call_args = mock_retrieve.call_args
    assert call_args.args[0] == user_text
    assert call_args.args[1] == conv_id

    # The retrieved older turns appear in the assembled context...
    contents = [m.content for m in result]
    assert any("design the architecture" in c for c in contents)
    assert any("three-tier memory" in c for c in contents)
    # ...and the recent window (current prompt) is preserved at the end.
    assert result[-1].content == user_text
    # A framing system note leads the retrieved block.
    assert result[0].role == "system"


def test_elastic_context_falls_back_when_no_history():
    """When Qdrant returns nothing (early turns / no matches),
    _elastic_context falls back to _trim_conversation (recent turns only).

    Graceful degradation: the system never breaks — it just falls back
    to the dumb window. Defends: docs/operations.md edge cases.
    """
    messages = _msgs(
        ("user", "Hello"),
        ("assistant", "Hi there"),
        ("user", "What is AIOS about?"),
    )
    user_text = "What is AIOS about?"
    conv_id = "conv-empty"

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]):
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    # No retrieved turns, so we just get the recent window (trimmed).
    # The current prompt is still the last message.
    assert result[-1].content == user_text
    # No retrieved-content system note framing older turns beyond the
    # standard _trim_conversation note (which only appears when trimmed).
    # Either way, the recent prompt is preserved.
    assert all("design the architecture" not in m.content for m in result)


def test_elastic_context_falls_back_on_retrieval_error():
    """If the embedding server / Qdrant is unreachable, retrieval raises
    and _elastic_context falls back to _trim_conversation. The system
    never breaks. Defends: docs/operations.md graceful
    degradation."""
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI OS."),
        ("user", "Tell me more about that architecture."),
    )
    user_text = "Tell me more about that architecture."
    conv_id = "conv-down"

    with patch("services.aios_core.main.retrieve_conversation_turns",
               side_effect=ConnectionError("embedding server down")):
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    # Fell back to recent turns; current prompt preserved.
    assert result[-1].content == user_text


def test_elastic_context_deduplicates_against_recent_turns():
    """Retrieved turns whose content is already in the recent window are
    dropped, so the LLM doesn't see the same turn twice.

    Defends: docs/operations.md "Duplicate turns: deduplicate
    by content hash."
    """
    recent_user = "Tell me about the architecture."
    recent_asst = "It uses a three-tier Qdrant memory."
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI OS."),
        ("user", recent_user),
        ("assistant", recent_asst),
        ("user", "Connect that to the memory system."),
    )
    user_text = "Connect that to the memory system."
    conv_id = "conv-dedup"

    # Qdrant returns one turn that's ALREADY in the recent window (dup)
    # and one genuinely older turn.
    fake_hits = [
        {"role": "user", "content": recent_user, "conversation_id": conv_id},
        {"role": "assistant", "content": "Older insight about memory tiers.",
         "conversation_id": conv_id, "timestamp": "t-50"},
    ]

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=fake_hits):
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    contents = [m.content for m in result]
    # The duplicate (recent_user) appears exactly once — it's in the
    # recent window, not duplicated from retrieval.
    assert sum(1 for c in contents if c == recent_user) == 1
    # The genuinely older retrieved turn IS included.
    assert any("Older insight about memory tiers" in c for c in contents)


def test_elastic_context_trivial_prompt_skips_retrieval():
    """Trivial prompts (greetings, 'yes', 'ok') skip the embedding +
    Qdrant query entirely — just use recent turns. Saves ~50ms on the
    easy cases. Defends: docs/operations.md trivial prompt
    shortcut."""
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI OS."),
        ("user", "ok"),
    )
    user_text = "ok"
    conv_id = "conv-trivial"

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[{"role": "user",
                              "content": "should not be retrieved"}]) as mock_retrieve:
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    # Retrieval was NOT called (trivial shortcut).
    mock_retrieve.assert_not_called()
    # Recent prompt preserved.
    assert result[-1].content == "ok"
    # The would-be-retrieved content is absent.
    assert all("should not be retrieved" not in m.content for m in result)


def test_elastic_context_respects_char_budget():
    """The assembled context is capped to ~6000 chars. When retrieved
    turns would overflow, oldest retrieved turns are dropped first
    (keeping the note + recent window). Defends: docs/operations.md
    token budget."""
    # Build a recent window + a long current prompt.
    long_a = "A" * 2000
    long_b = "B" * 2000
    messages = _msgs(
        ("user", long_a),
        ("assistant", long_b),
        ("user", "Connect that to the memory system."),
    )
    user_text = "Connect that to the memory system."
    conv_id = "conv-budget"

    # Retrieved turns that together would blow the 6000 char budget.
    fake_hits = [
        {"role": "user", "content": "X" * 3000, "conversation_id": conv_id,
         "timestamp": "t-10"},
        {"role": "assistant", "content": "Y" * 3000, "conversation_id": conv_id,
         "timestamp": "t-11"},
    ]

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=fake_hits):
        result = asyncio.run(_elastic_context(messages, user_text, conv_id, max_chars=6000))

    total = sum(len(m.content) for m in result)
    assert total <= 6000
    # The recent window (current prompt) is always preserved.
    assert result[-1].content == user_text


def test_elastic_context_enriches_vague_queries():
    """Vague context_reference prompts ("Tell me more about that.")
    produce generic embeddings that match nothing in Qdrant — the
    semantic anchor is in the previous turn. _elastic_context must
    enrich the retrieval query with the previous assistant reply so the
    embedding has something specific to match on.

    The enriched text is used ONLY for the Qdrant query; the LLM still
    sees the raw user prompt as the final message. Defends:
    docs/operations.md "Remaining Issues" #3 and the
    deflection collapse at turns 443-478 ("I don't know what 'that' is").
    """
    prev_assistant = "AIOS uses a three-tier Qdrant memory: working, project, longterm."
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI operating system."),
        ("user", "Tell me about the architecture."),
        ("assistant", prev_assistant),
        ("user", "Tell me more about that."),
    )
    user_text = "Tell me more about that."
    conv_id = "conv-vague"

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]) as mock_retrieve:
        asyncio.run(_elastic_context(messages, user_text, conv_id))

    mock_retrieve.assert_called_once()
    query_arg = mock_retrieve.call_args.args[0]
    # The query was enriched — it is NOT the raw prompt...
    assert query_arg != user_text
    # ...it carries the previous assistant reply as a context anchor...
    assert "three-tier Qdrant memory" in query_arg
    # ...and the raw prompt is still embedded as the leading text.
    assert query_arg.startswith(user_text)
    # Sanity: the heuristic agrees this is vague.
    assert _is_vague_reference(user_text) is True


def test_elastic_context_does_not_enrich_specific_queries():
    """Specific prompts ("Remember that architecture we discussed
    earlier?") carry their own semantics (the content word "architecture")
    and must NOT be enriched — enrichment would only add noise to a query
    that already retrieves well. Guards against over-triggering the
    vague-query heuristic. Defends: docs/operations.md."""
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI operating system."),
        ("user", "Remember that architecture we discussed earlier?"),
    )
    user_text = "Remember that architecture we discussed earlier?"
    conv_id = "conv-specific"

    # Heuristic must NOT flag this — it has the content word "architecture".
    assert _is_vague_reference(user_text) is False

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]) as mock_retrieve:
        asyncio.run(_elastic_context(messages, user_text, conv_id))

    mock_retrieve.assert_called_once()
    # Query passed to retrieval is the raw prompt, unmodified.
    assert mock_retrieve.call_args.args[0] == user_text


def test_is_vague_reference_heuristic_boundary():
    """Boundary cases for the vague-reference detector. Defends the
    enrichment trigger so it fires on anaphoric prompts and stays silent
    on self-contained ones."""
    # Vague: pronoun-heavy, no content words.
    assert _is_vague_reference("Tell me more about that.") is True
    assert _is_vague_reference("Connect that to it.") is True
    assert _is_vague_reference("What about the other one?") is True
    assert _is_vague_reference("Can you elaborate on that?") is True
    # Specific: carries a content noun, retrieves fine unaided.
    assert _is_vague_reference("Remember that architecture we discussed earlier?") is False
    assert _is_vague_reference("Connect that to the memory system.") is False
    assert _is_vague_reference("What is AIOS about?") is False
    # No deictic at all -> not a context_reference.
    assert _is_vague_reference("Hello") is False
    # Too long to be a terse anaphoric prompt.
    assert _is_vague_reference("that " + "x" * 120) is False


def test_elastic_context_fallback_uses_full_recent_window():
    """Regression: when retrieval returns nothing, the fallback must use
    the SAME recent-window size as _trim_conversation's default (6), not
    a shrunk window. Previously the fallback passed recent_turns=3, so
    the prompts that most need context (vague prompts that retrieve
    nothing) got LESS context than the dumb window. Defends:
    docs/operations.md graceful degradation — the fallback is
    a safety net, not a shrink ray."""
    # 8 messages: with recent_turns=6 the fallback keeps the last 6; with
    # the old bug (3) it would keep only the last 3.
    messages = _msgs(
        ("user", "msg one"),
        ("assistant", "reply one"),
        ("user", "msg two"),
        ("assistant", "reply two"),
        ("user", "msg three"),
        ("assistant", "reply three"),
        ("user", "msg four"),
        ("assistant", "reply four"),
    )
    user_text = "msg four"
    conv_id = "conv-fallback"

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]):
        result = asyncio.run(_elastic_context(messages, user_text, conv_id))

    contents = [m.content for m in result]
    # The fallback kept 6 recent messages (3 pairs), not 3. "msg two" is
    # the 3rd-from-last pair and must survive; with the old bug it would
    # be dropped.
    assert "msg two" in contents
    assert "reply two" in contents


def test_enrich_query_skips_deflection_anchors():
    """When the previous assistant reply is a deflection ("I don't have
    access to past conversations"), _enrich_query must NOT anchor on it.
    Anchoring on a deflection retrieves MORE deflections from Qdrant — a
    positive feedback loop that makes the deflection collapse worse. The
    enrichment must scan backwards past deflections to find the last
    SUBSTANTIVE reply.

    Defends: the cascading failure observed in the 500-turn stress test
    (turns 325-339 all deflecting, enrichment anchoring on deflections).
    """
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "AIOS is a local-first AI operating system with three-tier memory."),
        ("user", "Tell me about the architecture."),
        ("assistant", "I don't have access to past conversations. Each session is independent."),
        ("user", "Tell me more about that."),
    )
    user_text = "Tell me more about that."

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]) as mock_retrieve:
        asyncio.run(_elastic_context(messages, user_text, "conv-skip-defl"))

    mock_retrieve.assert_called_once()
    query_arg = mock_retrieve.call_args.args[0]
    # The query was enriched — NOT the raw prompt...
    assert query_arg != user_text
    # ...and it anchored on the SUBSTANTIVE reply (turn 2), NOT the
    # deflection (turn 4). The anchor must mention "local-first AI" or
    # "three-tier memory", NOT "don't have access".
    assert "local-first AI" in query_arg or "three-tier memory" in query_arg
    assert "don't have access" not in query_arg.lower()
    assert "each session" not in query_arg.lower()


def test_enrich_query_returns_raw_when_all_replies_are_deflections():
    """If ALL prior assistant replies are deflections (deep in a collapse),
    _enrich_query returns the raw prompt unchanged. Better to retrieve
    nothing than to anchor on a deflection and retrieve more deflections.
    Defends: the cascading failure's worst case."""
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "I don't know. I don't have access to past conversations."),
        ("user", "Tell me more about that."),
    )
    user_text = "Tell me more about that."

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]) as mock_retrieve:
        asyncio.run(_elastic_context(messages, user_text, "conv-all-defl"))

    mock_retrieve.assert_called_once()
    # Query is the raw prompt — no enrichment (nothing substantive to anchor on).
    assert mock_retrieve.call_args.args[0] == user_text


def test_elastic_context_filters_self_matching_hits():
    """A vague prompt like "Tell me more about that." appears dozens of
    times in a long conversation. Qdrant returns those past instances as
    "hits" because the query embedding matches itself. They're useless —
    the LLM would see its own past prompts, not any answers. The elastic
    context must filter them out.

    Defends: the self-matching failure observed in Qdrant diagnostics
    (Test 1: raw vague prompt returned 5 hits, all "Tell me more about
    that." from previous turns)."""
    user_text = "Tell me more about that."
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "A local-first AI OS."),
        ("user", user_text),
    )

    # Qdrant returns: 2 self-matching hits (past instances of the same
    # prompt) + 1 substantive hit (an actual answer about architecture).
    fake_hits = [
        {"role": "user", "content": "Tell me more about that.",
         "conversation_id": "conv-self", "timestamp": "t-50"},
        {"role": "user", "content": "Tell me more about that.",
         "conversation_id": "conv-self", "timestamp": "t-100"},
        {"role": "assistant",
         "content": "The architecture uses a three-tier Qdrant memory system.",
         "conversation_id": "conv-self", "timestamp": "t-105"},
    ]

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=fake_hits):
        result = asyncio.run(_elastic_context(messages, user_text, "conv-self"))

    contents = [m.content for m in result]
    # The self-matching hits are filtered out — "Tell me more about that."
    # appears only once (as the current prompt, the last message).
    assert sum(1 for c in contents if c == user_text) == 1
    # The substantive hit IS included.
    assert any("three-tier Qdrant memory" in c for c in contents)


def test_elastic_context_filters_deflection_hits():
    """Retrieved hits that are themselves deflections ("I don't have
    access to past conversations") must be filtered out. Feeding past
    deflections back as "relevant context" causes a cascading feedback
    loop: the LLM sees deflections and deflects more.

    Defends: the cascading failure observed in Qdrant diagnostics (Test 2:
    enriched-with-deflection query returned 5 hits, all deflections)."""
    user_text = "Tell me more about that."
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "AIOS uses a three-tier Qdrant memory: working, project, longterm."),
        ("user", user_text),
    )

    # Qdrant returns: 2 deflection hits + 1 substantive hit.
    fake_hits = [
        {"role": "assistant",
         "content": "I don't have access to previous conversations. Each session is independent.",
         "conversation_id": "conv-defl", "timestamp": "t-50"},
        {"role": "assistant",
         "content": "I cannot recall what was said before.",
         "conversation_id": "conv-defl", "timestamp": "t-60"},
        {"role": "assistant",
         "content": "The three-tier memory uses working, project, and longterm Qdrant collections.",
         "conversation_id": "conv-defl", "timestamp": "t-105"},
    ]

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=fake_hits):
        result = asyncio.run(_elastic_context(messages, user_text, "conv-defl"))

    contents = [m.content for m in result]
    # Deflection hits are filtered out.
    assert all("don't have access" not in c for c in contents)
    assert all("cannot recall" not in c for c in contents)
    # The substantive hit IS included.
    assert any("three-tier memory" in c for c in contents)


def test_is_deflection_detects_contracted_and_uncontracted():
    """_is_deflection must catch both contracted ("I don't know") and
    uncontracted ("I do not know") forms. The 500-turn stress test showed
    the LLM using both forms interchangeably. Defends: the deflection
    filter's coverage."""
    assert _is_deflection("I don't know what 'that' refers to.") is True
    assert _is_deflection("I do not know what 'that' refers to.") is True
    assert _is_deflection("I don't have access to past conversations.") is True
    assert _is_deflection("I do not have access to past conversations.") is True
    assert _is_deflection("I cannot recall previous conversations.") is True
    assert _is_deflection("I can't recall what was said before.") is True
    assert _is_deflection("I have no record of our conversation history.") is True
    assert _is_deflection("Each session is independent.") is True
    # Substantive content is NOT a deflection.
    assert _is_deflection("The architecture uses a three-tier Qdrant memory.") is False
    assert _is_deflection("AIOS is a local-first AI operating system.") is False
    assert _is_deflection("We should prioritize the stability gate next.") is False


def test_elastic_context_retrieves_more_hits_for_filtering_pool():
    """The retrieval limit is 20 (not 10) because the deflection filter
    and self-match filter discard many hits. In a long conversation where
    deflections have cascaded, most of the top-10 hits may be deflections
    or self-matches; with limit=20 we reach past them to real content.

    Defends: the need for a large enough pool of retrieved hits that
    filtering leaves substantive content, not an empty list."""
    user_text = "Tell me more about that."
    messages = _msgs(
        ("user", "What is AIOS about?"),
        ("assistant", "AIOS uses a three-tier Qdrant memory."),
        ("user", user_text),
    )

    with patch("services.aios_core.main.retrieve_conversation_turns",
               return_value=[]) as mock_retrieve:
        asyncio.run(_elastic_context(messages, user_text, "conv-limit"))

    mock_retrieve.assert_called_once()
    assert mock_retrieve.call_args.kwargs.get("limit", None) == 20 or \
           (len(mock_retrieve.call_args.args) > 2 and mock_retrieve.call_args.args[2] == 20)


def test_oai_request_elastic_context_defaults_off():
    """elastic_context defaults to False — existing clients that don't
    send the flag get _trim_conversation and behave identically.
    Backwards compat. Defends: docs/operations.md opt-in."""
    req = OAIChatRequest(messages=[OAIMessage(role="user", content="hi")])
    assert req.elastic_context is False
    # And it can be opted in.
    req_on = OAIChatRequest(
        messages=[OAIMessage(role="user", content="hi")],
        elastic_context=True,
        conversation_id="conv-1",
    )
    assert req_on.elastic_context is True


def test_oai_chat_completions_backwards_compat_no_elastic_flag(client):
    """Without elastic_context set, /v1/chat/completions uses
    _trim_conversation (NOT _elastic_context). Existing clients are
    unaffected. Defends: docs/operations.md opt-in / backwards
    compat."""
    with patch("services.aios_core.main._retrieve_memory_batch",
               return_value=[]), \
         patch("services.aios_core.main._call_llm_with_messages",
               return_value="ok") as mock_llm, \
         patch("services.aios_core.main._elastic_context",
               new=AsyncMock(side_effect=AssertionError(
                   "_elastic_context should NOT be called without the flag"))) \
         as mock_elastic, \
         patch("services.aios_core.main.verify_and_log", return_value="ok"), \
         patch("services.aios_core.main._persist_raw_async"), \
         patch("services.aios_core.main._maybe_web_search", return_value=""), \
         patch("services.aios_core.main._maybe_inject_system_status", return_value=""), \
         patch("services.aios_core.main._graph_rag_expand", return_value=""):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "aios-core",
                "messages": [{"role": "user", "content": "What is AIOS about?"}],
                "stream": False,
                "conversation_id": "conv-backcompat",
            },
        )
    assert response.status_code == 200
    mock_elastic.assert_not_called()
    mock_llm.assert_called_once()


def test_oai_chat_completions_uses_elastic_when_flagged(client):
    """With elastic_context=True AND a conversation_id, the endpoint
    routes through _elastic_context. Defends: docs/operations.md
    wiring."""
    with patch("services.aios_core.main._retrieve_memory_batch",
               return_value=[]), \
         patch("services.aios_core.main._call_llm_with_messages",
               return_value="ok"), \
         patch("services.aios_core.main._elastic_context",
               new=AsyncMock(return_value=[OAIMessage(role="user", content="hi")])) \
         as mock_elastic, \
         patch("services.aios_core.main.verify_and_log", return_value="ok"), \
         patch("services.aios_core.main._persist_raw_async"), \
         patch("services.aios_core.main._maybe_web_search", return_value=""), \
         patch("services.aios_core.main._maybe_inject_system_status", return_value=""), \
         patch("services.aios_core.main._graph_rag_expand", return_value=""):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "aios-core",
                "messages": [{"role": "user", "content": "What is AIOS about?"}],
                "stream": False,
                "conversation_id": "conv-elastic-on",
                "elastic_context": True,
            },
        )
    assert response.status_code == 200
    mock_elastic.assert_called_once()


