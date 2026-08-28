"""Unit tests for the ProjectSettings request/response schemas (no DB)."""

import pytest
from pydantic import ValidationError

from app.api.projects import SettingsIn, SettingsOut


def test_settings_in_defaults_match_db():
    s = SettingsIn()
    assert s.sandbox_port == 9000
    assert s.sandbox_container_port == 80
    assert s.rag_top_k == 8
    assert s.rag_max_chars == 12000
    assert s.mcp_servers is None
    assert s.model_default is None


def test_settings_in_enforces_port_and_rag_bounds():
    valid = SettingsIn(sandbox_port=8080, rag_top_k=16)
    assert valid.sandbox_port == 8080
    with pytest.raises(ValidationError):
        SettingsIn(sandbox_port=0)
    with pytest.raises(ValidationError):
        SettingsIn(sandbox_container_port=99999)
    with pytest.raises(ValidationError):
        SettingsIn(rag_top_k=0)
    with pytest.raises(ValidationError):
        SettingsIn(rag_max_chars=0)


def test_settings_out_carries_settings_payload():
    out = SettingsOut(
        project_id="00000000-0000-0000-0000-000000000000",
        sandbox_port=8080,
        sandbox_container_port=3000,
        rag_top_k=16,
        rag_max_chars=20000,
        mcp_servers=[{"name": "notion", "type": "http", "config": {}},
                     {"name": "postgres", "type": "http", "config": {"api_key": "k"}}],
        model_default="qwen3.5:4b",
        updated_at="2026-01-01T00:00:00Z",
    )
    data = out.model_dump()
    assert data["sandbox_port"] == 8080
    assert len(data["mcp_servers"]) == 2
    assert data["mcp_servers"][0]["name"] == "notion"
    assert data["model_default"] == "qwen3.5:4b"
