"""Static tests for proposal-tools registration in fix_tooling.py."""
import importlib.util
import os

FIX_TOOLING = os.path.join(os.path.dirname(__file__), "fix_tooling.py")


def _load_fix_tooling():
    spec = importlib.util.spec_from_file_location("fix_tooling", FIX_TOOLING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_proposal_tools_are_attached_only_to_deepdive():
    mod = _load_fix_tooling()
    assert mod.PROPOSAL_MODELS == {"qwen-deepdive"}
    assert mod.proposal_server_tool_ids_for("qwen-deepdive") == [
        "server:proposal-tools"
    ]
    for model_id in mod.ALL_QWEN_DEVSTRAL:
        if model_id != "qwen-deepdive":
            assert mod.proposal_server_tool_ids_for(model_id) == []


def test_proposal_tools_connection_and_filter_are_canonical():
    mod = _load_fix_tooling()
    conn = mod.PROPOSAL_TOOLS_CONNECTION
    assert conn["url"] == "http://127.0.0.1:8775"
    assert conn["info"]["id"] == "proposal-tools"
    assert conn["config"]["enable"] is True
    assert conn["config"]["function_name_filter_list"] == (
        "propose_host_change,list_proposals,proposal_status,"
        "retract_proposal"
    )
    for needle in ("proposal_models", "proposal_server_tool_ids"):
        assert needle in mod.TOOL_SERVER_FILTER_SOURCE


def test_proposal_service_unit_IS_hardened():
    """Inverse of the sandbox-tools constraint: no nested bwrap userns is
    needed, so the unit SHOULD carry ProtectHome=read-only — a host-adjacent
    channel that can't scribble outside its state dirs."""
    service_path = os.path.join(
        os.path.dirname(__file__), "proposal-tools.service")
    text = open(service_path).read()
    assert "ProtectHome=read-only" in text
    assert "ReadWritePaths=" in text
    assert "proposal-tools" in text
