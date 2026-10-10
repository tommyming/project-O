"""LlamaClient: streaming OpenAI-compatible client against a mock server."""

import json

import pytest

from o_chat.model_client import LlamaClient, LlamaServerError, StreamEvent

from mock_server import MockLlamaServer, sse_event

SSE_TEXT = (
    'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def test_stream_event_defaults():
    e = StreamEvent()
    assert e.text == "" and e.tool_calls is None and e.finish is None


def test_text_deltas_concatenate():
    with MockLlamaServer([SSE_TEXT]) as mock:
        got = "".join(
            e.text
            for e in LlamaClient(mock.url).chat(
                [{"role": "user", "content": "hi"}]
            )
        )
        assert got == "hello"


def test_tool_calls_surface_on_one_event():
    body = (
        sse_event(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c1",
                        "function": {
                            "name": "install_packages",
                            "arguments": json.dumps({"names": ["htop"]}),
                        },
                    }
                ]
            }
        )
        + "data: [DONE]\n\n"
    )
    with MockLlamaServer([body]) as mock:
        events = list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))
    tool_events = [e for e in events if e.tool_calls]
    assert len(tool_events) == 1
    assert tool_events[0].tool_calls[0]["function"]["name"] == "install_packages"


def test_finish_reason_surfaces():
    body = sse_event(finish="stop") + "data: [DONE]\n\n"
    with MockLlamaServer([body]) as mock:
        events = list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))
    assert any(e.finish == "stop" for e in events)


def test_done_sentinel_stops_stream():
    body = (
        sse_event(delta={"content": "a"})
        + "data: [DONE]\n\n"
        + sse_event(delta={"content": "IGNORED"})
    )
    with MockLlamaServer([body]) as mock:
        got = "".join(
            e.text for e in LlamaClient(mock.url).chat([{"role": "user", "content": "x"}])
        )
    assert got == "a"


def test_partial_json_chunks_ignored():
    body = (
        "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"cont\n\n"
        "data: [DONE]\n\n"
    )
    with MockLlamaServer([body]) as mock:
        got = "".join(
            e.text for e in LlamaClient(mock.url).chat([{"role": "user", "content": "x"}])
        )
    assert got == "ok"


def test_request_records_tools_and_grammar():
    tools = [{"type": "function", "function": {"name": "sys_info", "parameters": {}}}]
    body = sse_event(delta={"content": "hi"}) + "data: [DONE]\n\n"
    with MockLlamaServer([body]) as mock:
        list(
            LlamaClient(mock.url).chat(
                [{"role": "user", "content": "x"}], tools=tools, grammar="root ::= \"x\""
            )
        )
        req = mock.requests[0]
    assert req["tools"] == tools
    assert req["grammar"] == "root ::= \"x\""
    assert req["stream"] is True


def test_grammar_omitted_when_not_passed():
    body = sse_event(delta={"content": "hi"}) + "data: [DONE]\n\n"
    with MockLlamaServer([body]) as mock:
        list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))
        req = mock.requests[0]
    assert "grammar" not in req


def test_midstream_close_without_done_raises(tmp_path=None):
    # Server closes cleanly after partial text but never sends [DONE]:
    # must surface as LlamaServerError, not a fabricated "done" turn.
    body = 'data: {"choices":[{"delta":{"content":"partial"}}]\n\n'
    with MockLlamaServer([{"no_done": body}]) as mock:
        with pytest.raises(LlamaServerError):
            list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))


def test_midstream_broken_chunked_raises_not_raw():
    # Server declares more bytes than it sends: http.client raises
    # IncompleteRead — must be wrapped in LlamaServerError.
    body = 'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
    with MockLlamaServer([{"truncate": body}]) as mock:
        with pytest.raises(LlamaServerError):
            list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))


def test_http_500_raises():
    with MockLlamaServer([500]) as mock:
        with pytest.raises(LlamaServerError):
            list(LlamaClient(mock.url).chat([{"role": "user", "content": "x"}]))


def test_connection_refused_raises():
    client = LlamaClient("http://127.0.0.1:1")
    with pytest.raises(LlamaServerError):
        list(client.chat([{"role": "user", "content": "x"}]))
