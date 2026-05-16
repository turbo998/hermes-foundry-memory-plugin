"""Tests for the LLM tool layer (OpenAI function-calling adapters)."""
from __future__ import annotations

import json

import pytest

from hermes_foundry_memory.client import MemoryRecord, Message, MockFoundryClient
from hermes_foundry_memory.tools import get_tool_schemas, handle_tool_call


@pytest.fixture
def client():
    return MockFoundryClient()


def _calls_of(client, name):
    return [c for c in client.calls if c[0] == name]


# --- Schemas ---

def test_get_tool_schemas_returns_three_named_tools():
    schemas = get_tool_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) == 3
    names = [s["name"] for s in schemas]
    assert names == ["azurememory_search", "azurememory_list", "azurememory_recent"]


def test_schemas_have_openai_function_calling_shape():
    for s in get_tool_schemas():
        assert isinstance(s["name"], str)
        assert isinstance(s["description"], str) and s["description"]
        params = s["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert isinstance(params["required"], list)


def test_search_schema_requires_query():
    schemas = {s["name"]: s for s in get_tool_schemas()}
    assert "query" in schemas["azurememory_search"]["parameters"]["required"]


# --- search ---

def test_search_calls_client_and_returns_results(client):
    client.preset_search_results = [
        MemoryRecord(content="hello world"),
        MemoryRecord(content="foo bar"),
    ]
    out = handle_tool_call(
        client, "azurememory_search", {"query": "hi", "top_k": 2},
        thread_id="t1", user_id="u1",
    )
    data = json.loads(out)
    assert data["count"] == 2
    assert len(data["results"]) == 2
    assert data["results"][0]["content"] == "hello world"
    call = _calls_of(client, "search_long_term")[-1]
    # ("search_long_term", query, scope, namespace, top_k)
    assert call[1] == "hi"
    assert call[4] == 2


def test_search_default_top_k_is_5(client):
    client.preset_search_results = [MemoryRecord(content=f"r{i}") for i in range(10)]
    handle_tool_call(client, "azurememory_search", {"query": "q"}, thread_id="t", user_id="u")
    call = _calls_of(client, "search_long_term")[-1]
    assert call[4] == 5


def test_search_clamps_top_k(client):
    handle_tool_call(client, "azurememory_search", {"query": "q", "top_k": 999}, thread_id="t", user_id="u")
    assert _calls_of(client, "search_long_term")[-1][4] == 20
    handle_tool_call(client, "azurememory_search", {"query": "q", "top_k": 0}, thread_id="t", user_id="u")
    assert _calls_of(client, "search_long_term")[-1][4] == 1
    handle_tool_call(client, "azurememory_search", {"query": "q", "top_k": -7}, thread_id="t", user_id="u")
    assert _calls_of(client, "search_long_term")[-1][4] == 1


def test_search_missing_query_returns_error(client):
    out = handle_tool_call(client, "azurememory_search", {}, thread_id="t", user_id="u")
    assert "error" in json.loads(out)


# --- list ---

def test_list_calls_client_and_returns_results(client):
    client.records[("u", None)] = [MemoryRecord(content="a"), MemoryRecord(content="b")]
    out = handle_tool_call(client, "azurememory_list", {"max_results": 2}, thread_id="t", user_id="u")
    data = json.loads(out)
    assert data["count"] == 2
    assert data["results"][1]["content"] == "b"
    call = _calls_of(client, "list_long_term")[-1]
    # ("list_long_term", scope, namespace, max_results)
    assert call[3] == 2


def test_list_default_max_results_is_20(client):
    handle_tool_call(client, "azurememory_list", {}, thread_id="t", user_id="u")
    assert _calls_of(client, "list_long_term")[-1][3] == 20


def test_list_clamps_max_results(client):
    handle_tool_call(client, "azurememory_list", {"max_results": 5000}, thread_id="t", user_id="u")
    assert _calls_of(client, "list_long_term")[-1][3] == 100
    handle_tool_call(client, "azurememory_list", {"max_results": 0}, thread_id="t", user_id="u")
    assert _calls_of(client, "list_long_term")[-1][3] == 1


# --- recent ---

def test_recent_returns_role_content_turns(client):
    client.threads["t1"] = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]
    out = handle_tool_call(client, "azurememory_recent", {"k": 2}, thread_id="t1", user_id="u1")
    data = json.loads(out)
    assert data["count"] == 2
    assert data["turns"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    call = _calls_of(client, "get_last_k_turns")[-1]
    # ("get_last_k_turns", thread_id, k)
    assert call[1] == "t1"
    assert call[2] == 2


def test_recent_default_k_is_5(client):
    client.threads["t"] = [Message(role="user", content=str(i)) for i in range(10)]
    handle_tool_call(client, "azurememory_recent", {}, thread_id="t", user_id="u")
    assert _calls_of(client, "get_last_k_turns")[-1][2] == 5


def test_recent_clamps_k(client):
    handle_tool_call(client, "azurememory_recent", {"k": 999}, thread_id="t", user_id="u")
    assert _calls_of(client, "get_last_k_turns")[-1][2] == 20
    handle_tool_call(client, "azurememory_recent", {"k": 0}, thread_id="t", user_id="u")
    assert _calls_of(client, "get_last_k_turns")[-1][2] == 1


# --- error handling ---

def test_unknown_tool_returns_error(client):
    out = handle_tool_call(client, "azurememory_bogus", {}, thread_id="t", user_id="u")
    assert json.loads(out) == {"error": "unknown tool: azurememory_bogus"}


def test_client_exception_wrapped_as_error(client):
    client.fail_reads = True
    out = handle_tool_call(client, "azurememory_search", {"query": "x"}, thread_id="t", user_id="u")
    data = json.loads(out)
    assert "error" in data
    assert "fail_reads" in data["error"]


def test_handle_tool_call_returns_string(client):
    out = handle_tool_call(client, "azurememory_list", {}, thread_id="t", user_id="u")
    assert isinstance(out, str)
    json.loads(out)
