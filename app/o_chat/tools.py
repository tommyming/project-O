"""The six tools the model may call: schemas, validation, GBNF retry grammar.

Small models are unreliable emitters of tool calls, so every call is
validated against the schema, and retries may be grammar-constrained
(spec section 5.4).
"""

from typing import Any

TOOL_NAMES: tuple[str, ...] = (
    "run_command",
    "search_packages",
    "install_packages",
    "remove_packages",
    "list_installed",
    "sys_info",
)


class ToolValidationError(Exception):
    """A tool call that does not match its schema."""


_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command on the system. Explain it first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "explanation": {"type": "string"},
                    "privileged": {"type": "boolean"},
                },
                "required": ["cmd", "explanation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_packages",
            "description": "Search the local package repository.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_packages",
            "description": "Install packages from the local repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_packages",
            "description": "Remove installed packages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_installed",
            "description": "List installed packages.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sys_info",
            "description": "Report RAM, disk, kernel, and uptime.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI function-format schemas for all six tools."""
    return [s.copy() for s in _TOOL_SCHEMAS]


def _require_str(args: dict, key: str, tool: str) -> None:
    if key not in args:
        raise ToolValidationError(f"{tool}: missing required argument '{key}'")
    if not isinstance(args[key], str):
        raise ToolValidationError(f"{tool}: '{key}' must be a string")


def validate_tool_call(call: Any) -> tuple[str, dict[str, Any]]:
    """Validate a tool call against its schema; return (name, args).

    Raises ToolValidationError on unknown tools, missing required
    arguments, or wrong argument types.
    """
    if not isinstance(call, dict) or not isinstance(call.get("name"), str):
        raise ToolValidationError("tool call must be an object with a 'name' string")
    name = call["name"]
    if name not in TOOL_NAMES:
        raise ToolValidationError(f"unknown tool: {name!r}")
    args = call.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolValidationError(f"{name}: 'arguments' must be an object")

    if name == "run_command":
        _require_str(args, "cmd", name)
        _require_str(args, "explanation", name)
        if "privileged" in args and not isinstance(args["privileged"], bool):
            raise ToolValidationError(f"{name}: 'privileged' must be a boolean")
    elif name == "search_packages":
        _require_str(args, "query", name)
    elif name in ("install_packages", "remove_packages"):
        names = args.get("names")
        if not isinstance(names, list) or not all(
            isinstance(n, str) for n in names
        ):
            raise ToolValidationError(f"{name}: 'names' must be a list of strings")
    # list_installed / sys_info take no typed arguments.

    return name, args


_GRAMMAR_TEMPLATE = r'''root   ::= "{" ws "\"name\"" ws ":" ws toolname ws "," ws "\"arguments\"" ws ":" ws object ws "}"
toolname ::= {name_alternation}
''' + r'''string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]) )* "\""
ws ::= | " " | "\n" [ \t]{0,20}
object ::= "{" ws ( string ws ":" ws value (ws "," ws string ws ":" ws value)* ws )? "}"
value ::= object | array | string | number | boolean | null
array ::= "[" ws ( value (ws "," ws value)* ws )? "]"
number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})?
boolean ::= "true" | "false"
null ::= "null"
'''


def tool_call_grammar() -> str:
    """GBNF grammar forcing a tool call: one of the six names + JSON args.

    Applied on retry requests after a malformed call (spec section 5.4).
    """
    alternation = " | ".join(f'"\\"{name}\\""' for name in TOOL_NAMES)
    return _GRAMMAR_TEMPLATE.replace("{name_alternation}", alternation)
