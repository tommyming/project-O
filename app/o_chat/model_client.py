"""Streaming OpenAI-compatible client for llama-server.

stdlib-only (urllib): the OS image carries no extra HTTP dependency.
Spec section 5.5: llama-server serves 127.0.0.1:8080 with --jinja.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LlamaServerError(Exception):
    """The model server is unreachable or misbehaving."""


@dataclass
class StreamEvent:
    text: str = ""
    tool_calls: list[dict] | None = None
    finish: str | None = None


@dataclass
class LlamaClient:
    base_url: str = "http://127.0.0.1:8080"
    timeout: int = 300

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        grammar: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream one chat completion as StreamEvents.

        Raises LlamaServerError on connection failures or non-200
        responses. Malformed/partial SSE JSON chunks are skipped.
        """
        payload: dict[str, Any] = {"messages": messages, "stream": True}
        if tools is not None:
            payload["tools"] = tools
        if grammar is not None:
            payload["grammar"] = grammar

        request = Request(
            f"{self.base_url.rstrip('/')}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as e:
            raise LlamaServerError(f"server returned HTTP {e.code}") from e
        except (URLError, ConnectionError, TimeoutError, OSError) as e:
            raise LlamaServerError(f"cannot reach model server: {e}") from e

        with response:
            if response.status != 200:
                raise LlamaServerError(f"server returned HTTP {response.status}")
            yield from self._parse_sse(response)

    def _parse_sse(self, response) -> Iterator[StreamEvent]:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue  # partial chunk; skip per spec
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or ""
            tool_calls = delta.get("tool_calls")
            finish = choice.get("finish_reason")
            if text or tool_calls or finish:
                yield StreamEvent(text=text, tool_calls=tool_calls, finish=finish)
