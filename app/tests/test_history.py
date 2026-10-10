"""History: append-only JSONL persistence of OpenAI-shaped messages."""

import json

from o_chat.history import History


def test_append_and_reload_roundtrip(tmp_path):
    path = tmp_path / "history.jsonl"
    h = History(path)
    h.append(role="user", content="install htop")
    h.append(
        role="assistant",
        content=None,
        tool_calls=[
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "install_packages",
                    "arguments": json.dumps({"names": ["htop"]}),
                },
            }
        ],
    )
    h.append(role="tool", tool_call_id="call_0", content="exit=0")
    h.append(role="assistant", content="Installed htop.")

    reloaded = History(path).messages()
    assert reloaded == [
        {"role": "user", "content": "install htop"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {
                        "name": "install_packages",
                        "arguments": json.dumps({"names": ["htop"]}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "exit=0"},
        {"role": "assistant", "content": "Installed htop."},
    ]


def test_file_is_one_json_object_per_line(tmp_path):
    path = tmp_path / "history.jsonl"
    h = History(path)
    h.append(role="user", content="a")
    h.append(role="assistant", content="b")
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"role": "user", "content": "a"}
    assert json.loads(lines[1]) == {"role": "assistant", "content": "b"}


def test_missing_file_yields_empty(tmp_path):
    assert History(tmp_path / "nope.jsonl").messages() == []


def test_corrupt_lines_skipped(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text(
        '{"role": "user", "content": "good"}\n'
        "THIS IS NOT JSON\n"
        '{"role": "assistant", "content": "fine"}\n'
    )
    assert History(path).messages() == [
        {"role": "user", "content": "good"},
        {"role": "assistant", "content": "fine"},
    ]


def test_plain_message_shape_omits_optional_fields(tmp_path):
    h = History(tmp_path / "h.jsonl")
    h.append(role="user", content="hi")
    (msg,) = h.messages()
    assert msg == {"role": "user", "content": "hi"}
