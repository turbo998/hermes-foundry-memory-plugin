"""End-to-end integration test for register() and the hook chain."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import hermes_foundry_memory
from hermes_foundry_memory import FoundryMemoryProvider, register
from hermes_foundry_memory.client import MemoryRecord, MockFoundryClient


class FakeCtx:
    """Minimal stand-in for the Hermes plugin host context."""

    def __init__(self) -> None:
        self.providers: list = []

    def register_memory_provider(self, provider) -> None:
        self.providers.append(provider)


def _patch_provider_with_mock(monkeypatch, mock_client):
    """Replace FoundryMemoryProvider in __init__ so register() uses our mock."""
    original = FoundryMemoryProvider

    def factory(*args, **kwargs):
        kwargs.pop("client", None)
        return original(*args, client=mock_client, **kwargs)

    monkeypatch.setattr(hermes_foundry_memory, "FoundryMemoryProvider", factory)


def test_register_returns_and_registers_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    mock = MockFoundryClient()
    _patch_provider_with_mock(monkeypatch, mock)

    ctx = FakeCtx()
    provider = register(ctx)

    assert isinstance(provider, FoundryMemoryProvider)
    assert ctx.providers == [provider]
    assert provider.hermes_home == tmp_path
    # default config flowed through
    assert provider.config["memory_store_name"] == "hermes_user_mem"


def test_register_loads_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg_path = tmp_path / "foundry_memory.json"
    cfg_path.write_text(json.dumps({"user_id": "alice", "memory_store_name": "custom"}))

    mock = MockFoundryClient()
    _patch_provider_with_mock(monkeypatch, mock)

    ctx = FakeCtx()
    provider = register(ctx)

    assert provider.config["user_id"] == "alice"
    assert provider.config["memory_store_name"] == "custom"


def test_register_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_USER_ID", "bob")

    mock = MockFoundryClient()
    _patch_provider_with_mock(monkeypatch, mock)

    ctx = FakeCtx()
    provider = register(ctx)
    assert provider.config["user_id"] == "bob"


def test_full_hook_chain_end_to_end(tmp_path, monkeypatch):
    """initialize → on_memory_write → sync_turn → handle_tool_call → shutdown."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    mock = MockFoundryClient(
        preset_search_results=[MemoryRecord(content="hello world", namespace="/m/")],
    )
    _patch_provider_with_mock(monkeypatch, mock)

    ctx = FakeCtx()
    provider = register(ctx)

    # 1. initialize (primary context triggers a pull from cloud)
    provider.initialize(session_id="sess-1", user_id="alice", agent_context="primary")
    assert provider.session_id == "sess-1"
    assert provider.user_id == "alice"

    # 2. on_memory_write → local file updated + cloud sync enqueued
    provider.on_memory_write("memory", "add", "hello")
    assert (tmp_path / "memories" / "MEMORY.md").exists()

    # 3. sync_turn → add_turns enqueued
    provider.sync_turn("q", "a")

    # Drain the worker queue so async ops land before we assert.
    assert provider._wait_idle(timeout=2.0)

    # 4. handle_tool_call (synchronous)
    raw = provider.handle_tool_call("azurememory_search", {"query": "h"})
    payload = json.loads(raw)
    assert payload["count"] == 1
    assert payload["results"][0]["content"] == "hello world"

    # 5. shutdown
    provider.shutdown()

    # Assert the mock client recorded the expected interactions.
    ops = [c[0] for c in mock.calls]
    assert "list_long_term" in ops  # initialize pull
    assert "batch_create_records" in ops  # memory_sync from on_memory_write
    assert "add_turns" in ops  # sync_turn
    assert "search_long_term" in ops  # tool call

    # add_turns payload sanity
    add_calls = [c for c in mock.calls if c[0] == "add_turns"]
    assert add_calls[0][1] == "sess-1"
    assert add_calls[0][2] == "alice"
    msgs = add_calls[0][3]
    assert msgs[0].role == "user" and msgs[0].content == "q"
    assert msgs[1].role == "assistant" and msgs[1].content == "a"

    # batch_create from on_memory_write should include 'hello'
    bc = [c for c in mock.calls if c[0] == "batch_create_records"]
    assert any("hello" in entries for _, _, _, entries in bc)
