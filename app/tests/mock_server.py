"""Scriptable mock of llama-server's OpenAI-compatible chat endpoint.

Each script entry is either a raw SSE body string (served as 200) or an
int (served as that HTTP status with an empty body). Requests are
recorded as parsed JSON bodies in FIFO order.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def sse_event(delta: dict | None = None, finish: str | None = None) -> str:
    """Build one SSE data line carrying a single choice."""
    choice: dict = {}
    if delta is not None:
        choice["delta"] = delta
    if finish is not None:
        choice["finish_reason"] = finish
    return f"data: {json.dumps({'choices': [choice]})}\n\n"


def SSE_TEXT(text: str) -> str:
    """A complete stream carrying one text answer."""
    return sse_event(delta={"content": text}) + "data: [DONE]\n\n"


def SSE_TOOL(name: str, arguments: dict, call_id: str = "call_1") -> str:
    """A complete stream carrying exactly one tool call."""
    tc = {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return sse_event(delta={"tool_calls": [tc]}) + "data: [DONE]\n\n"


def SSE_MIXED(text: str, name: str, arguments: dict, call_id: str = "call_1") -> str:
    """A stream with a text delta followed by a tool call."""
    tc = {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return (
        sse_event(delta={"content": text})
        + sse_event(delta={"tool_calls": [tc]})
        + "data: [DONE]\n\n"
    )


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockLlama/1.0"

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        self.server.requests.append(body)

        script = self.server.script
        index = min(self.server.served, len(script) - 1)
        entry = script[index]
        self.server.served += 1

        if isinstance(entry, int):
            self.send_response(entry)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if isinstance(entry, dict):
            if "no_done" in entry:
                payload = entry["no_done"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True
                return
            if "truncate" in entry:
                payload = entry["truncate"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                # Lie about the length: client read raises IncompleteRead.
                self.send_header("Content-Length", str(len(payload) + 64))
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True
                return

        payload = entry.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # silence request logging
        pass


class MockLlamaServer:
    """Run a scripted llama-server on 127.0.0.1:<ephemeral> as a context manager."""

    def __init__(self, script: list[str | int]):
        self.script = script
        self.requests: list[dict] = []
        self.served = 0
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.requests = self.requests
        self._httpd.script = self.script
        self._httpd.served = 0
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockLlamaServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
