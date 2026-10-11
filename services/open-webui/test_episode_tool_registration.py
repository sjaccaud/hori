"""Static tests for the HORI episode-write connection in fix_tooling.py.

The episode surface is a separate MCP connection entry (same endpoint,
admin-scoped bearer) so function-level gating stays per-model — only
EPISODE_MODELS get server:mcp:hori-episodes. The read-scoped hori-state
connection can never invoke episode_append.
"""
import importlib.util
import os

FIX_TOOLING = os.path.join(os.path.dirname(__file__), "fix_tooling.py")


def _load_fix_tooling():
    spec = importlib.util.spec_from_file_location("fix_tooling", FIX_TOOLING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_episode_write_attached_only_to_deepdive():
    mod = _load_fix_tooling()
    assert mod.EPISODE_MODELS == {"qwen-deepdive"}
    assert mod.episode_server_tool_ids_for("qwen-deepdive") == [
        "server:mcp:hori-episodes"
    ]
    for model_id in mod.ALL_QWEN_DEVSTRAL:
        if model_id != "qwen-deepdive":
            assert mod.episode_server_tool_ids_for(model_id) == []


def test_episode_connection_and_filter_are_canonical():
    mod = _load_fix_tooling()
    conn = mod.HORI_EPISODES_CONNECTION
    assert conn["url"] == "http://127.0.0.1:5680/mcp/http"
    assert conn["type"] == "mcp"
    assert conn["auth_type"] == "bearer"
    assert conn["info"]["id"] == "hori-episodes"
    assert conn["config"]["enable"] is True
    assert conn["config"]["function_name_filter_list"] == "episode_append"
    for needle in ("episode_models", "episode_server_tool_ids"):
        assert needle in mod.TOOL_SERVER_FILTER_SOURCE


def test_read_connection_stays_read_only():
    """The shared hori-state connection must never expose write tools."""
    mod = _load_fix_tooling()
    fns = mod.HORI_STATE_CONNECTION["config"]["function_name_filter_list"]
    assert "episode_append" not in fns
    assert "memory_store" not in fns
