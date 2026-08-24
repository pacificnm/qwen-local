"""Streaming chat client for the Ollama OpenAI-compatible endpoint.

Thin on purpose: the agent loop (agent.py) owns all control (event stream,
cancel, caps) instead of delegating to a heavier agent library.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.settings import get_settings

ConnectTimeout = 10.0
ReadTimeout = 300.0  # 27b on a V100: slow first tokens; keep the window wide


class LLMError(RuntimeError):
    """Ollama request failed; `detail` is a short, user-safe digest."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """Chain-of-thought from reasoning models (`delta.reasoning`); not the answer."""

    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments_chunk: str = ""


@dataclass(frozen=True)
class DoneDelta:
    finish_reason: str | None


Delta = TextDelta | ThinkingDelta | ToolCallDelta | DoneDelta

ShouldCancel = Callable[[], Awaitable[bool]] | Callable[[], bool]


class LLMClient:
    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.effective_ollama_host).rstrip("/")
        self.keep_alive = settings.ollama_keep_alive

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(ReadTimeout, connect=ConnectTimeout))

    async def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        should_cancel: ShouldCancel | None = None,
    ) -> AsyncIterator[Delta]:
        """Stream one model turn as deltas. Caller iterates to completion."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "keep_alive": self.keep_alive,
        }
        if tools:
            body["tools"] = tools

        async with self._client() as client:
            try:
                async with client.stream("POST", f"{self.base_url}/v1/chat/completions", json=body) as resp:
                    if resp.status_code >= 400:
                        await self._fail(resp)
                    async for line in resp.aiter_lines():
                        if should_cancel is not None and await _maybe_cancel(should_cancel):
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        delta = self._parse_delta(payload)
                        if delta is not None:
                            yield delta
            except httpx.HTTPStatusError as exc:
                raise LLMError(exc.detail if isinstance(exc.detail, str) else str(exc.detail)) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Ollama request failed: {exc.__class__.__name__}") from exc

    @staticmethod
    async def _fail(resp: httpx.Response) -> None:
        try:
            raw = (await resp.aread()).decode("utf-8", "replace")[:300].replace("\n", " ").strip()
        except httpx.HTTPError:
            raw = ""
        if resp.status_code == 413:
            raise LLMError("context too long (413): ask to narrow the scope")
        if resp.status_code in (400, 404):
            raise LLMError(f"Ollama rejected the request ({resp.status_code}): {raw[:120]}")
        raise LLMError(f"Ollama error {resp.status_code}: {raw[:120]}")

    @staticmethod
    def _parse_delta(payload: str) -> Delta | None:
        try:
            data: Any = json.loads(payload)
        except ValueError:
            return None
        choice = (data.get("choices") or [{}])[0]
        d = choice.get("delta") or {}
        if d.get("reasoning"):
            return ThinkingDelta(text=d["reasoning"])
        if d.get("content"):
            return TextDelta(text=d["content"])
        for tc in d.get("tool_calls") or []:
            fn = tc.get("function") or {}
            return ToolCallDelta(
                index=int(tc.get("index", 0)),
                id=tc.get("id"),
                name=fn.get("name"),
                arguments_chunk=fn.get("arguments") or "",
            )
        if choice.get("finish_reason"):
            return DoneDelta(finish_reason=choice["finish_reason"])
        return None


async def _maybe_cancel(should_cancel: ShouldCancel) -> bool:
    result = should_cancel()
    if hasattr(result, "__await__"):
        result = await result  # type: ignore[valid-type]
    return bool(result)
