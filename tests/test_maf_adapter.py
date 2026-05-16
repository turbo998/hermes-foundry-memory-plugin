"""Tests for the Microsoft Agent Framework (MAF) adapter layer.

The adapter only loads when the optional ``agent-framework-core`` dependency
is installed; otherwise these tests are skipped.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("agent_framework")

import agent_framework as af  # noqa: E402

from hermes_foundry_memory import maf_adapter  # noqa: E402


def _make_client() -> MagicMock:
    client = MagicMock()
    client.search_long_term.return_value = [
        {"content": "hello", "strategy": "semantic", "namespace": "ns"}
    ]
    client.list_long_term.return_value = [
        {"content": "rec-1", "strategy": None, "namespace": "ns"}
    ]
    client.get_last_k_turns.return_value = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    return client


def test_get_maf_tools_returns_3_function_tools():
    client = _make_client()
    tools = maf_adapter.get_maf_tools(
        client=client, thread_id="t-1", user_id="u-1"
    )
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"foundry_search", "foundry_list", "foundry_recent"}


def test_each_tool_has_name_and_description():
    client = _make_client()
    tools = maf_adapter.get_maf_tools(
        client=client, thread_id="t-1", user_id="u-1"
    )
    for t in tools:
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.description, str) and t.description


def test_tools_are_function_tool_instances():
    client = _make_client()
    tools = maf_adapter.get_maf_tools(
        client=client, thread_id="t-1", user_id="u-1"
    )
    for t in tools:
        assert isinstance(t, af._tools.FunctionTool)


def test_foundry_search_tool_invocable():
    client = _make_client()
    tools = {
        t.name: t
        for t in maf_adapter.get_maf_tools(
            client=client, thread_id="t-1", user_id="u-1"
        )
    }
    search = tools["foundry_search"]
    # Underlying function should be directly callable for unit testing.
    result = search.func(query="hello", top_k=3)
    assert isinstance(result, list)
    assert result and result[0]["content"] == "hello"
    client.search_long_term.assert_called_once()
    kwargs = client.search_long_term.call_args.kwargs
    assert kwargs["query"] == "hello"
    assert kwargs["top_k"] == 3
    assert kwargs["scope"] == "u-1"


def test_foundry_list_tool_invocable():
    client = _make_client()
    tools = {
        t.name: t
        for t in maf_adapter.get_maf_tools(
            client=client, thread_id="t-1", user_id="u-1"
        )
    }
    out = tools["foundry_list"].func(max_results=10)
    assert isinstance(out, list)
    client.list_long_term.assert_called_once()


def test_foundry_recent_tool_invocable():
    client = _make_client()
    tools = {
        t.name: t
        for t in maf_adapter.get_maf_tools(
            client=client, thread_id="t-1", user_id="u-1"
        )
    }
    out = tools["foundry_recent"].func(k=2)
    assert isinstance(out, list) and len(out) == 2
    client.get_last_k_turns.assert_called_once_with(thread_id="t-1", k=2)
