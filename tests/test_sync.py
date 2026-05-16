"""Tests for sync paths: initialize pull, breaker, ha_backup."""
from __future__ import annotations

import pytest

from hermes_foundry_memory.client import MemoryRecord, MockFoundryClient
from hermes_foundry_memory.provider import FoundryMemoryProvider
from hermes_foundry_memory.storage import read_entries


def test_initialize_primary_pulls_cloud(tmp_path):
    client = MockFoundryClient()
    client.records[("u1", "/builtin-primary/memory/")] = [
        MemoryRecord(content="cloud-mem-1"),
        MemoryRecord(content="cloud-mem-2"),
    ]
    client.records[("u1", "/builtin-primary/user/")] = [
        MemoryRecord(content="cloud-user-1"),
    ]
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": True, "user_id": "u1"},
        hermes_home=tmp_path,
    )
    try:
        p.initialize("sess", "u1", agent_context="primary")
        mem = read_entries(tmp_path / "memories" / "MEMORY.md")
        usr = read_entries(tmp_path / "memories" / "USER.md")
        assert mem == ["cloud-mem-1", "cloud-mem-2"]
        assert usr == ["cloud-user-1"]
    finally:
        p.shutdown()


def test_initialize_non_primary_context_no_pull(tmp_path):
    client = MockFoundryClient()
    client.records[("u1", "/builtin-primary/memory/")] = [MemoryRecord(content="x")]
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": True, "user_id": "u1"},
        hermes_home=tmp_path,
    )
    try:
        p.initialize("sess", "u1", agent_context="child")
        # no list_long_term calls
        assert not [c for c in client.calls if c[0] == "list_long_term"]
        assert not (tmp_path / "memories" / "MEMORY.md").exists()
    finally:
        p.shutdown()


def test_initialize_primary_mode_disabled(tmp_path):
    client = MockFoundryClient()
    client.records[("u1", "/builtin-primary/memory/")] = [MemoryRecord(content="x")]
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": False, "user_id": "u1"},
        hermes_home=tmp_path,
    )
    try:
        p.initialize("sess", "u1", agent_context="primary")
        assert not [c for c in client.calls if c[0] == "list_long_term"]
    finally:
        p.shutdown()


def test_breaker_open_skips_writes(tmp_path):
    client = MockFoundryClient(fail_writes=True)
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": True, "user_id": "u1"},
        hermes_home=tmp_path,
    )
    try:
        p.initialize("s", "u1", agent_context="other")
        # Force breaker open
        for _ in range(10):
            p._breaker.record_failure()
        assert p._breaker.is_open()

        # Should not raise
        for i in range(3):
            p.sync_turn(f"u{i}", f"a{i}")
        p._wait_idle(timeout=2.0)
        # No add_turns should have been actually invoked (skipped)
        assert not [c for c in client.calls if c[0] == "add_turns"]
    finally:
        p.shutdown()


def test_ha_backup_mirrors(tmp_path):
    client = MockFoundryClient()
    p = FoundryMemoryProvider(
        client=client,
        config={"primary_mode": True, "user_id": "u1", "ha_backup": True},
        hermes_home=tmp_path,
    )
    try:
        p.initialize("s", "u1", agent_context="other")
        p.on_memory_write("memory", "add", "fact")
        p._wait_idle(timeout=2.0)
        bc = [c for c in client.calls if c[0] == "batch_create_records"]
        namespaces = {c[2] for c in bc}
        assert "/builtin-primary/memory/" in namespaces
        assert "/builtin-backup/memory/" in namespaces
    finally:
        p.shutdown()
