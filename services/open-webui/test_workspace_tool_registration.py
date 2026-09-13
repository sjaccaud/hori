"""Static tests for workspace-tools registration in fix_tooling.py."""
import ast
import importlib.util
import os

FIX_TOOLING = os.path.join(os.path.dirname(__file__), "fix_tooling.py")


def _load_fix_tooling():
    spec = importlib.util.spec_from_file_location("fix_tooling", FIX_TOOLING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workspace_write_tools_are_attached_only_to_deepdive():
    mod = _load_fix_tooling()
    assert mod.WORKSPACE_WRITE_MODELS == {"qwen-deepdive"}
    assert mod.workspace_server_tool_ids_for("qwen-deepdive") == [
        "server:workspace-tools"
    ]
    for model_id in mod.ALL_QWEN_DEVSTRAL:
        if model_id != "qwen-deepdive":
            assert mod.workspace_server_tool_ids_for(model_id) == []


def test_workspace_tools_connection_and_prompt_are_canonical():
    mod = _load_fix_tooling()
    conn = mod.WORKSPACE_TOOLS_CONNECTION
    assert conn["url"] == "http://127.0.0.1:8771"
    assert conn["info"]["id"] == "workspace-tools"
    assert conn["config"]["enable"] is True
    assert conn["config"]["function_name_filter_list"] == (
        "propose_workspace_write,confirm_workspace_write,rollback_workspace_write"
    )

    prompt = mod.system_prompt_for("qwen-deepdive")
    for needle in (
        "propose_workspace_write",
        "confirm_workspace_write",
        "rollback_workspace_write",
        "relative paths under the configured Projects workspace",
    ):
        assert needle in prompt

    assert "write_models" in mod.TOOL_SERVER_FILTER_SOURCE
    assert "write_server_tool_ids" in mod.TOOL_SERVER_FILTER_SOURCE


def test_workspace_tools_service_confines_writes_to_projects_workspace():
    service_path = os.path.join(os.path.dirname(__file__), "workspace-tools.service")
    text = open(service_path).read()
    assert "WORKSPACE_TOOLS_ROOT=%h/Projects" in text
    assert "ProtectHome=read-only" in text
    assert "ReadWritePaths=%h/Projects %h/.local/share/aios" in text
    assert "WORKSPACE_TOOLS_PORT=8771" in text


def test_tool_connection_loop_does_not_shadow_sqlite_connection():
    tree = ast.parse(open(FIX_TOOLING).read())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    for node in ast.walk(main):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            assert node.target.id != "conn"
