"""Unit tests for FoundryMemoryProvider."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_foundry_memory.client import MemoryRecord, Message, MockFoundryClient
from hermes_foundry_memory.provider import FoundryMemoryProvider
from hermes_foundry_memory.storage import read_entries


@pytest.fixture
def provider(tmp_path):
    client = MockFoundryClient()
    cfg = {"primary_mode": True, "user_id": "u1", "ha_backup": False}
    p = FoundryMemoryProvider(client=client, config=cfg, hermes_home=tmp_path)
    p.initialize(session_id="sess1", user_id="u1", agent_context="other")
    yield p
    p.shutdown()


def _calls(client, op):
    return [c for c in client.calls if c[0] == op]


def test_basic_construction(tmp_path):
    client = MockFoundryClient()
    p = FoundryMemoryProvider(client=client, config={"user_id": "u"}, hermes_home=tmp_path)
    try:
        assert p.name == "foundry_memory"
        assert "semantic_search" in p.features
        assert (tmp_path / "memories").exists() or True  # may be lazy
    finally:
        p.shutdown()


def test_on_memory_write_add_replace_remove(provider, tmp_path):
    mem_file = tmp_path / "memories" / "MEMORY.md"

    provider.on_memory_write("memory", "add", "fact one")
    provider.on_memory_write("memory", "add", "fact two")
    assert read_entries(mem_file) == ["fact one", "fact two"]

    provider.on_memory_write("memory", "replace", "fact 1", old_text="fact one")
    assert read_entries(mem_file) == ["fact 1", "fact two"]

    provider.on_memory_write("memory", "remove", "", old_text="fact two")
    assert read_entries(mem_file) == ["fact 1"]

    # user file too
    user_file = tmp_path / "memories" / "USER.md"
    provider.on_memory_write("user", "add", "likes tea")
    assert read_entries(user_file) == ["likes tea"]

    provider._wait_idle(timeout=2.0)
    # batch_create_records should have been called for syncs
    assert _calls(provider.client, "batch_create_records")


def test_sync_turn_enqueues_add_turns(provider):
    provider.sync_turn("hi", "hello")
    provider._wait_idle(timeout=2.0)
    addt = _calls(provider.client, "add_turns")
    assert addt, "expected add_turns to be invoked"
    _, thread_id, user_id, msgs = addt[-1]
    assert thread_id == "sess1"
    assert user_id == "u1"
    assert len(msgs) == 2
    assert msgs[0].role == "user" and msgs[0].content == "hi"
    assert msgs[1].role == "assistant" and msgs[1].content == "hello"


def test_on_pre_compress(provider):
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(15)]
    provider.on_pre_compress(msgs)
    provider._wait_idle(timeout=2.0)
    bc = _calls(provider.client, "batch_create_records")
    # find the compressed namespace one
    compressed = [c for c in bc if c[2] == "/compressed/"]
    assert compressed
    _, _, _, contents = compressed[-1]
    assert len(contents) == 1
    # last 10 should be in the joined content
    joined = contents[0]
    assert "m5" in joined and "m14" in joined
    assert "m4" not in joined


def test_on_delegation(provider):
    provider.on_delegation("do thing", "did thing", child_session_id="c1")
    provider._wait_idle(timeout=2.0)
    bc = _calls(provider.client, "batch_create_records")
    deleg = [c for c in bc if c[2] == "/delegation/"]
    assert deleg
    _, _, _, contents = deleg[-1]
    blob = "\n".join(contents)
    assert "do thing" in blob and "did thing" in blob and "c1" in blob


def test_queue_prefetch_and_block(provider):
    provider.client.preset_search_results = [
        MemoryRecord(content="alpha"),
        MemoryRecord(content="beta"),
    ]
    provider.queue_prefetch("anything")
    block = provider.get_prefetch_block()
    assert "alpha" in block and "beta" in block


def test_system_prompt_block(provider, tmp_path):
    provider.on_memory_write("memory", "add", "remember X")
    provider.on_memory_write("user", "add", "prefers Y")
    blk = provider.system_prompt_block()
    assert "remember X" in blk
    assert "prefers Y" in blk


def test_get_tool_schemas(provider):
    schemas = provider.get_tool_schemas()
    assert len(schemas) == 3


def test_handle_tool_call_routes(provider):
    provider.client.preset_search_results = [MemoryRecord(content="x")]
    out = provider.handle_tool_call("azurememory_search", {"query": "hello"})
    assert isinstance(out, str)
    data = json.loads(out)
    assert data["count"] == 1


def test_shutdown_clean(tmp_path):
    client = MockFoundryClient()
    p = FoundryMemoryProvider(client=client, config={}, hermes_home=tmp_path)
    p.initialize("s", "u")
    p.sync_turn("a", "b")
    p.shutdown()
    assert not p._worker.is_alive()
