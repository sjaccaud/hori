"""Static tests for sandbox-tools registration in fix_tooling.py."""
import importlib.util
import os

FIX_TOOLING = os.path.join(os.path.dirname(__file__), "fix_tooling.py")


def _load_fix_tooling():
    spec = importlib.util.spec_from_file_location("fix_tooling", FIX_TOOLING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sandbox_tools_are_attached_only_to_deepdive():
    mod = _load_fix_tooling()
    assert mod.SANDBOX_MODELS == {"qwen-deepdive"}
    assert mod.sandbox_server_tool_ids_for("qwen-deepdive") == [
        "server:sandbox-tools"
    ]
    for model_id in mod.ALL_QWEN_DEVSTRAL:
        if model_id != "qwen-deepdive":
            assert mod.sandbox_server_tool_ids_for(model_id) == []


def test_sandbox_tools_connection_and_filter_are_canonical():
    mod = _load_fix_tooling()
    conn = mod.SANDBOX_TOOLS_CONNECTION
    assert conn["url"] == "http://127.0.0.1:8774"
    assert conn["info"]["id"] == "sandbox-tools"
    assert conn["config"]["enable"] is True
    assert conn["config"]["function_name_filter_list"] == (
        "sandbox_exec,sandbox_status"
    )
    for needle in ("sandbox_models", "sandbox_server_tool_ids"):
        assert needle in mod.TOOL_SERVER_FILTER_SOURCE


def test_sandbox_service_unit_is_a_plain_launcher():
    """bwrap needs to create a userns; systemd mount-ns hardening puts the
    service inside one already and the kernel's userns restriction blocks
    the nested creation — so the unit must NOT carry those directives."""
    service_path = os.path.join(os.path.dirname(__file__), "sandbox-tools.service")
    text = open(service_path).read()
    for banned in ("ProtectHome=", "ProtectSystem=", "PrivateTmp=",
                   "NoNewPrivileges=", "ReadWritePaths="):
        assert banned not in text, banned
    assert "SANDBOX_TOOLS_PORT=8774" in text
    assert "SANDBOX_SCRATCH=%h/agent-scratch" in text
