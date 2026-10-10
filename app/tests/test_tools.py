"""Tool schemas, tool-call validation, and the GBNF retry grammar."""

import pytest

from o_chat.tools import (
    ToolValidationError,
    tool_call_grammar,
    tool_schemas,
    validate_tool_call,
)

EXPECTED_TOOLS = {
    "run_command",
    "search_packages",
    "install_packages",
    "remove_packages",
    "list_installed",
    "sys_info",
}


def test_six_tools_with_exact_names():
    names = {t["function"]["name"] for t in tool_schemas()}
    assert names == EXPECTED_TOOLS


def test_run_command_schema():
    schema = next(t for t in tool_schemas() if t["function"]["name"] == "run_command")
    props = schema["function"]["parameters"]["properties"]
    assert props["cmd"]["type"] == "string"
    assert props["explanation"]["type"] == "string"
    assert props["privileged"]["type"] == "boolean"
    required = schema["function"]["parameters"].get("required", [])
    assert "cmd" in required and "explanation" in required
    assert "privileged" not in required


def test_package_tools_take_name_lists():
    for tool in ("install_packages", "remove_packages"):
        schema = next(t for t in tool_schemas() if t["function"]["name"] == tool)
        params = schema["function"]["parameters"]
        assert params["properties"]["names"]["type"] == "array"
        assert params["properties"]["names"]["items"]["type"] == "string"
        assert params["required"] == ["names"]


def test_search_takes_query():
    schema = next(t for t in tool_schemas() if t["function"]["name"] == "search_packages")
    params = schema["function"]["parameters"]
    assert params["properties"]["query"]["type"] == "string"
    assert params["required"] == ["query"]


def test_readonly_tools_take_no_args():
    for tool in ("list_installed", "sys_info"):
        schema = next(t for t in tool_schemas() if t["function"]["name"] == tool)
        assert schema["function"]["parameters"]["properties"] == {}


def test_validate_roundtrip():
    name, args = validate_tool_call(
        {"name": "install_packages", "arguments": {"names": ["htop", "jq"]}})
    assert name == "install_packages" and args == {"names": ["htop", "jq"]}


def test_unknown_tool_rejected():
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "delete_everything", "arguments": {}})


def test_names_must_be_list_of_strings():
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "install_packages", "arguments": {"names": "htop"}})
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "install_packages", "arguments": {"names": [1, 2]}})


def test_missing_required_arg_rejected():
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "run_command", "arguments": {"cmd": "ls"}})


def test_wrong_arg_type_rejected():
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "run_command",
                            "arguments": {"cmd": 42, "explanation": "x"}})


def test_no_args_tools_accept_empty():
    name, args = validate_tool_call({"name": "sys_info", "arguments": {}})
    assert name == "sys_info" and args == {}


def test_grammar_names_all_six_tools():
    grammar = tool_call_grammar()
    for name in EXPECTED_TOOLS:
        assert name in grammar
