"""Contract tests: invoke provider hooks the way the Hermes MemoryManager does.

These tests guard against silent contract drift between
:class:`FoundryMemoryProvider` and the host's
``agent.memory_provider.MemoryProvider`` interface used by
``hermes-agentcore-memory`` (the reference plugin).

Hooks are dispatched the way the host actually calls them:
    - positional args for the documented signature
    - kwargs for fields that may legitimately not be known to the plugin
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_foundry_memory.client import MemoryRecord, MockFoundryClient
from hermes_foundry_memory.provider import FoundryMemoryProvider
from hermes_foundry_memory.storage import read_entries


@pytest.fixture
def provider(tmp_path):
    client = MockFoundryClient()
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": False, "user_id": "alice"},
        hermes_home=tmp_path,
    )
    yield p
    p.shutdown()


# -- initialize ----------------------------------------------------------------

def test_initialize_accepts_full_host_kwargs(provider, tmp_path):
    """Host passes hermes_home, agent_identity, agent_workspace, and friends."""
    provider.initialize(
        "sess-xyz",
        user_id="alice",
        agent_context="primary",
        hermes_home=tmp_path,
        agent_identity={"name": "primary"},
        agent_workspace=tmp_path / "ws",
        # tomorrow the host could add anything; we must tolerate it.
        future_kwarg="ignored",
    )
    assert provider.session_id == "sess-xyz"
    assert provider.user_id == "alice"


def test_initialize_positional_session_id_only(provider):
    provider.initialize("session-1")
    assert provider.session_id == "session-1"


# -- on_memory_write -----------------------------------------------------------

def test_on_memory_write_positional_action_target_content(provider, tmp_path):
    """Host signature: on_memory_write(action, target, content)."""
    provider.initialize("s", user_id="alice", agent_context="other")
    provider.on_memory_write("add", "memory", "fact A")
    mem_file = tmp_path / "memories" / "MEMORY.md"
    assert read_entries(mem_file) == ["fact A"]


def test_on_memory_write_replace_and_remove(provider, tmp_path):
    provider.initialize("s", user_id="alice", agent_context="other")
    provider.on_memory_write("add", "user", "p=1")
    provider.on_memory_write("replace", "user", "p=2", old_text="p=1")
    assert read_entries(tmp_path / "memories" / "USER.md") == ["p=2"]
    provider.on_memory_write("remove", "user", "", old_text="p=2")
    assert read_entries(tmp_path / "memories" / "USER.md") == []


# -- sync_turn -----------------------------------------------------------------

def test_sync_turn_positional_user_assistant_session_kwarg(provider):
    provider.initialize("default-sess", user_id="alice")
    provider.sync_turn("question", "answer", session_id="explicit-sess")
    provider._wait_idle(timeout=2.0)
    calls = [c for c in provider.client.calls if c[0] == "add_turns"]
    assert calls
    assert calls[-1][1] == "explicit-sess"


def test_sync_turn_tolerates_extra_kwargs(provider):
    provider.initialize("s", user_id="alice")
    # host may add fields in the future
    provider.sync_turn("q", "a", session_id="s", turn_number=4)
    provider._wait_idle(timeout=2.0)


# -- on_pre_compress -----------------------------------------------------------

def test_on_pre_compress_returns_str_for_summary(provider):
    provider.initialize("s", user_id="alice")
    out = provider.on_pre_compress(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    )
    assert isinstance(out, str)
    assert "hello" in out


# -- on_session_end ------------------------------------------------------------

def test_on_session_end_persists_session(provider):
    provider.initialize("s", user_id="alice")
    provider.on_session_end([
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ])
    provider._wait_idle(timeout=2.0)
    bc = [c for c in provider.client.calls if c[0] == "batch_create_records"]
    assert any(c[2] == "/sessions/" for c in bc)


# -- on_turn_start -------------------------------------------------------------

def test_on_turn_start_positional_turn_and_message(provider):
    provider.initialize("s", user_id="alice")
    # must not raise
    provider.on_turn_start(1, {"role": "user", "content": "hi"})
    provider.on_turn_start(2, {"role": "user", "content": "yo"}, extra="ok")


# -- on_delegation -------------------------------------------------------------

def test_on_delegation_positional_with_child_session_kwarg(provider):
    provider.initialize("s", user_id="alice")
    provider.on_delegation("task X", "result Y", child_session_id="child-1")
    provider._wait_idle(timeout=2.0)
    bc = [c for c in provider.client.calls if c[0] == "batch_create_records"]
    assert any(c[2] == "/delegation/" for c in bc)


def test_on_delegation_tolerates_extra_kwargs(provider):
    provider.initialize("s", user_id="alice")
    provider.on_delegation("t", "r", child_session_id="c", agent_name="sub")
    provider._wait_idle(timeout=2.0)


# -- handle_tool_call ----------------------------------------------------------

def test_handle_tool_call_positional_name_args_with_kwargs(provider):
    provider.initialize("s", user_id="alice")
    provider.client.preset_search_results = [MemoryRecord(content="hit")]
    out = provider.handle_tool_call(
        "azurememory_search", {"query": "x"}, call_id="abc", message_id="m1"
    )
    payload = json.loads(out)
    assert payload["count"] == 1


# -- has_tool / get_config_schema / get_tool_schemas --------------------------

def test_has_tool(provider):
    assert provider.has_tool("azurememory_search") is True
    assert provider.has_tool("not_a_tool") is False


def test_get_config_schema_has_required_fields(provider):
    schema = provider.get_config_schema()
    assert isinstance(schema, list)
    keys = {entry["key"] for entry in schema}
    assert {"foundry_endpoint", "memory_store_name", "api_key"} <= keys
    # at least one entry should declare itself required
    assert any(e.get("required") for e in schema)


def test_get_tool_schemas_shape(provider):
    schemas = provider.get_tool_schemas()
    assert schemas
    for s in schemas:
        assert "name" in s and "parameters" in s


# -- name / features -----------------------------------------------------------

def test_name_and_features(provider):
    assert provider.name == "foundry_memory"
    assert isinstance(provider.features, list)
