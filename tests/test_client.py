"""Tests for FoundryClient abstraction and MockFoundryClient."""
from __future__ import annotations

import abc
import os

import pytest

from hermes_foundry_memory.client import (
    AzureFoundryClient,
    FoundryClient,
    MemoryRecord,
    Message,
    MockFoundryClient,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
def test_message_dataclass_fields():
    m = Message(role="user", content="hi", timestamp="2025-01-01T00:00:00Z")
    assert m.role == "user"
    assert m.content == "hi"
    assert m.timestamp == "2025-01-01T00:00:00Z"


def test_message_timestamp_optional():
    m = Message(role="assistant", content="hello")
    assert m.timestamp is None


def test_memory_record_dataclass_fields():
    r = MemoryRecord(content="fact", strategy="summary", namespace="user/abc")
    assert r.content == "fact"
    assert r.strategy == "summary"
    assert r.namespace == "user/abc"


def test_memory_record_optional_fields():
    r = MemoryRecord(content="fact")
    assert r.strategy is None
    assert r.namespace is None


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------
def test_foundry_client_is_abstract():
    assert issubclass(FoundryClient, abc.ABC)
    with pytest.raises(TypeError):
        FoundryClient()  # type: ignore[abstract]


def test_foundry_client_abstract_methods_present():
    expected = {
        "add_turns",
        "search_long_term",
        "list_long_term",
        "get_last_k_turns",
        "batch_create_records",
        "delete_namespace",
    }
    assert expected.issubset(FoundryClient.__abstractmethods__)


# ---------------------------------------------------------------------------
# MockFoundryClient behavior
# ---------------------------------------------------------------------------
def test_mock_add_turns_records():
    c = MockFoundryClient()
    msgs = [Message("user", "hi"), Message("assistant", "hello")]
    c.add_turns("t1", "u1", msgs)
    assert len(c.threads["t1"]) == 2
    assert c.threads["t1"][0].content == "hi"
    assert c.calls[0][0] == "add_turns"


def test_mock_get_last_k_turns():
    c = MockFoundryClient()
    msgs = [Message("user", f"m{i}") for i in range(5)]
    c.add_turns("t1", "u1", msgs)
    last2 = c.get_last_k_turns("t1", 2)
    assert len(last2) == 2
    assert [m.content for m in last2] == ["m3", "m4"]


def test_mock_get_last_k_turns_empty_thread():
    c = MockFoundryClient()
    assert c.get_last_k_turns("nope", 3) == []


def test_mock_search_long_term_returns_preset():
    c = MockFoundryClient()
    preset = [MemoryRecord(content="abc", strategy="summary", namespace="user/u1")]
    c.preset_search_results = preset
    results = c.search_long_term("query", scope="user", namespace="user/u1", top_k=5)
    assert results == preset


def test_mock_list_long_term_returns_namespace_records():
    c = MockFoundryClient()
    c.batch_create_records("user", "user/u1", ["a", "b", "c"])
    out = c.list_long_term("user", "user/u1", max_results=10)
    assert len(out) == 3
    assert {r.content for r in out} == {"a", "b", "c"}


def test_mock_list_long_term_max_results():
    c = MockFoundryClient()
    c.batch_create_records("user", "user/u1", [f"x{i}" for i in range(10)])
    out = c.list_long_term("user", "user/u1", max_results=3)
    assert len(out) == 3


def test_mock_batch_create_records_writes():
    c = MockFoundryClient()
    c.batch_create_records("user", "user/u1", ["fact1", "fact2"])
    assert len(c.records[("user", "user/u1")]) == 2


def test_mock_delete_namespace_clears():
    c = MockFoundryClient()
    c.batch_create_records("user", "user/u1", ["x"])
    c.delete_namespace("user", "user/u1")
    assert ("user", "user/u1") not in c.records or c.records[("user", "user/u1")] == []


def test_mock_fail_writes_raises():
    c = MockFoundryClient(fail_writes=True)
    with pytest.raises(RuntimeError):
        c.add_turns("t1", "u1", [Message("user", "hi")])
    with pytest.raises(RuntimeError):
        c.batch_create_records("user", "user/u1", ["a"])


def test_mock_fail_reads_raises():
    c = MockFoundryClient(fail_reads=True)
    with pytest.raises(RuntimeError):
        c.search_long_term("q", "user", "user/u1", 5)
    with pytest.raises(RuntimeError):
        c.list_long_term("user", "user/u1", 5)
    with pytest.raises(RuntimeError):
        c.get_last_k_turns("t1", 3)


def test_mock_is_foundry_client():
    assert isinstance(MockFoundryClient(), FoundryClient)


# ---------------------------------------------------------------------------
# Azure client stub
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.environ.get("AZURE_FOUNDRY_LIVE"),
    reason="requires live Azure Foundry credentials",
)
def test_azure_client_skip_without_credentials():
    client = AzureFoundryClient(
        endpoint="https://example.cognitiveservices.azure.com",
        memory_store_name="store",
    )
    # If live env present, we still expect NotImplementedError for unimpl methods.
    with pytest.raises(NotImplementedError):
        client.add_turns("t", "u", [])


def test_azure_client_constructs_without_calling_azure():
    # Construction should not require Azure SDK to be installed/working.
    try:
        c = AzureFoundryClient(
            endpoint="https://example.cognitiveservices.azure.com",
            memory_store_name="store",
        )
    except ImportError:
        pytest.skip("azure-identity not installed")
    assert c.endpoint.startswith("https://")
    assert c.memory_store_name == "store"
