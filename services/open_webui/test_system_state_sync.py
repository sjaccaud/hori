"""Unit tests for services.open_webui.system_state_sync.

Only the curated project-decisions sync remains — the system-state
document path was retired 2026-09-23 (replaced by the injected
state-snapshot mechanism; see system_state_sync.py docstring).
"""


def test_upsert_knowledge_file_creates_collection_and_uploads():
    """upsert_knowledge_file signs in and uploads a knowledge file."""
    from unittest.mock import MagicMock, patch

    from services.open_webui.system_state_sync import upsert_knowledge_file

    def fake_post(url, *args, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "/auths/signin" in url:
            response.json.return_value = {"token": "TOKEN"}
        elif "/knowledge/create" in url:
            response.json.return_value = {
                "id": "knowledge-1",
                "name": "Local Benchmarks",
            }
        elif "/files/" in url:
            response.json.return_value = {
                "id": "file-1",
                "filename": "aios-system-state.md",
            }
        elif "/retrieval/process/file" in url:
            response.json.return_value = {"status": "ok"}
        elif "/knowledge/" in url and "/file/add" in url:
            response.json.return_value = {}
        else:
            response.json.return_value = {}
        return response

    def fake_get(url, *args, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "/knowledge/" in url:
            response.json.return_value = {"items": []}
        else:
            response.json.return_value = {}
        return response

    with patch(
        "services.open_webui.system_state_sync.requests.post", side_effect=fake_post
    ):
        with patch(
            "services.open_webui.system_state_sync.requests.get", side_effect=fake_get
        ):
            result = upsert_knowledge_file("# markdown")

    assert result["id"] == "file-1"
    assert result["filename"] == "aios-system-state.md"


def test_render_project_decisions_md_concatenates_sources(tmp_path):
    """render_project_decisions_md combines source files under per-source headings."""
    from services.open_webui.system_state_sync import render_project_decisions_md

    source_a = tmp_path / "a.md"
    source_a.write_text("# Alpha\n")
    source_b = tmp_path / "b.md"
    source_b.write_text("## Beta\n- item\n")

    md = render_project_decisions_md(
        sources=[source_a, source_b],
        allow_list=[tmp_path],
    )

    assert "## Source:" in md
    assert "# Alpha" in md
    assert "## Beta" in md
    assert "- item" in md


def test_sync_project_decisions_renders_and_upserts():
    """sync_project_decisions renders and returns the uploaded file id."""
    from unittest.mock import patch

    from services.open_webui.system_state_sync import sync_project_decisions

    with patch(
        "services.open_webui.system_state_sync.render_project_decisions_md",
        return_value="# decisions",
    ):
        with patch(
            "services.open_webui.system_state_sync.upsert_knowledge_file",
            return_value={"id": "file-99", "filename": "project-decisions.md"},
        ) as fake_upsert:
            result = sync_project_decisions()

    assert result == "file-99"
    assert fake_upsert.call_args[1]["filename"] == "project-decisions.md"
    assert fake_upsert.call_args[1]["collection_name"] == "Project Decisions & Architecture"


def test_find_knowledge_parses_dict_response():
    """_find_knowledge must find a collection inside a dict {'items': [...]}."""
    from unittest.mock import MagicMock, patch

    from services.open_webui.system_state_sync import _find_knowledge

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "items": [
            {"id": "other", "name": "Other"},
            {"id": "target", "name": "Local Benchmarks"},
        ]
    }

    with patch(
        "services.open_webui.system_state_sync.requests.get",
        return_value=response,
    ):
        found = _find_knowledge("TOKEN", "http://127.0.0.1:3000", "Local Benchmarks")

    assert found == "target"


def test_find_file_in_collection_parses_dict_response():
    """_find_file_in_collection must find a file inside a dict {'items': [...]}."""
    from unittest.mock import MagicMock, patch

    from services.open_webui.system_state_sync import _find_file_in_collection

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "items": [
            {"id": "other", "filename": "other.md"},
            {"id": "target", "filename": "aios-system-state.md"},
        ]
    }

    with patch(
        "services.open_webui.system_state_sync.requests.get",
        return_value=response,
    ):
        found = _find_file_in_collection(
            "TOKEN", "http://127.0.0.1:3000", "knowledge-id", "aios-system-state.md"
        )

    assert found == "target"
