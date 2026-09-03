"""Live Ollama model-discovery tests (no network).

No httpx-mocking precedent exists in this repo (all existing tests use
in-process fakes, see test_qwen_agent_adapter.py) — a small local fake
`httpx.AsyncClient` stands in for `respx`/similar here.
"""

import pytest

import app.api.models_api as models_api
import app.core.settings as cs


class FakeResponse:
    def __init__(self, json_body, status=200):
        self._json = json_body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeAsyncClient:
    """Records nothing, just returns canned responses by endpoint; a `fail`
    flag simulates Ollama being unreachable (connection error, not a 4xx)."""

    def __init__(self, tags_response=None, show_responses=None, fail=False, **_kwargs):
        self.tags_response = tags_response
        self.show_responses = show_responses or {}
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        if self.fail:
            raise RuntimeError("connection refused")
        assert url.endswith("/api/tags")
        return FakeResponse(self.tags_response)

    async def post(self, url, json=None, **kwargs):
        if self.fail:
            raise RuntimeError("connection refused")
        assert url.endswith("/api/show")
        return FakeResponse(self.show_responses.get(json["name"], {"parameters": ""}))


@pytest.fixture(autouse=True)
def _clear_context_cache():
    # _CONTEXT_CACHE is a process-lifetime dict — isolate tests from each other.
    models_api._CONTEXT_CACHE.clear()
    yield
    models_api._CONTEXT_CACHE.clear()


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(models_api.httpx, "AsyncClient", lambda **_: FakeAsyncClient(**kwargs))


async def test_list_models_maps_installed_and_marks_default(monkeypatch):
    settings = cs.Settings(
        ollama_strong_model="qwen3.8:27b-longctx",
        ollama_fast_model="qwen3.5:4b",
        ollama_compaction_model="qwen3.5:4b",
    )
    monkeypatch.setattr(models_api, "get_settings", lambda: settings)
    _patch_client(
        monkeypatch,
        tags_response={
            "models": [
                {
                    "model": "qwen3.8:27b-longctx",
                    "details": {"parameter_size": "27B", "quantization_level": "Q4_K_M"},
                },
                {"model": "qwen3.5:4b", "details": {}},
            ]
        },
        show_responses={
            "qwen3.8:27b-longctx": {"parameters": "num_ctx 32768\nstop foo"},
            "qwen3.5:4b": {"parameters": ""},
        },
    )

    result = await models_api.list_models()

    assert [m.id for m in result] == ["qwen3.8:27b-longctx", "qwen3.5:4b"]
    strong, fast = result
    assert strong.is_default is True
    assert strong.is_default_fast_chat is False
    assert strong.is_default_compaction is False
    assert strong.label == "qwen3.8:27b-longctx (27B, Q4_K_M)"
    assert strong.context_window == 32768
    assert fast.is_default is False
    # fast and compaction share the same default tag here (as they do in
    # Settings' own defaults) — both flags should be set on that one entry.
    assert fast.is_default_fast_chat is True
    assert fast.is_default_compaction is True
    assert fast.label == "qwen3.5:4b"
    assert fast.context_window is None


async def test_list_models_empty_when_ollama_unreachable(monkeypatch):
    settings = cs.Settings()
    monkeypatch.setattr(models_api, "get_settings", lambda: settings)
    _patch_client(monkeypatch, fail=True)

    result = await models_api.list_models()

    assert result == []


async def test_list_models_skips_entries_without_a_usable_id(monkeypatch):
    settings = cs.Settings()
    monkeypatch.setattr(models_api, "get_settings", lambda: settings)
    _patch_client(
        monkeypatch,
        tags_response={"models": [{"details": {}}, {"model": "ok:1"}]},
        show_responses={"ok:1": {"parameters": ""}},
    )

    result = await models_api.list_models()

    assert [m.id for m in result] == ["ok:1"]
