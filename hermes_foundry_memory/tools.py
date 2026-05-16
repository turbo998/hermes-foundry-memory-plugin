"""LLM tool layer exposing Azure Foundry memory operations as OpenAI-style function-calling tools."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "azurememory_search",
        "description": (
            "Semantic / hybrid search over the user's long-term Azure Foundry memory. "
            "Returns the top matching memory records for the given natural-language query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language query to search long-term memory.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1-20, default 5).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "azurememory_list",
        "description": (
            "List recent long-term memory records for the user without a search query. "
            "Useful for browsing what is stored."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of records to return (1-100, default 20).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        },
    },
    {
        "name": "azurememory_recent",
        "description": (
            "Fetch the last k turns from the current conversation thread (short-term memory)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "k": {
                    "type": "integer",
                    "description": "Number of recent turns to return (1-20, default 5).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": [],
        },
    },
]


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return the OpenAI function-calling tool schemas for Azure Foundry memory."""
    return [dict(s) for s in TOOL_SCHEMAS]


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _serialize(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    # Fallback: try __dict__
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return obj


def _handle_search(client: Any, args: dict[str, Any], *, thread_id: str, user_id: str) -> dict[str, Any]:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return {"error": "missing required argument: query"}
    top_k = _clamp(int(args.get("top_k", 5)), 1, 20)
    scope = args.get("scope", "user")
    namespace = args.get("namespace")
    results = client.search_long_term(query=query, scope=scope, namespace=namespace, top_k=top_k)
    serialized = [_serialize(r) for r in results]
    return {"results": serialized, "count": len(serialized)}


def _handle_list(client: Any, args: dict[str, Any], *, thread_id: str, user_id: str) -> dict[str, Any]:
    max_results = _clamp(int(args.get("max_results", 20)), 1, 100)
    scope = args.get("scope", "user")
    namespace = args.get("namespace")
    results = client.list_long_term(scope=scope, namespace=namespace, max_results=max_results)
    serialized = [_serialize(r) for r in results]
    return {"results": serialized, "count": len(serialized)}


def _handle_recent(client: Any, args: dict[str, Any], *, thread_id: str, user_id: str) -> dict[str, Any]:
    k = _clamp(int(args.get("k", 5)), 1, 20)
    turns = client.get_last_k_turns(thread_id=thread_id, k=k)
    serialized = []
    for t in turns:
        d = _serialize(t)
        if isinstance(d, dict):
            serialized.append({"role": d.get("role"), "content": d.get("content")})
        else:
            serialized.append({"role": getattr(t, "role", None), "content": getattr(t, "content", None)})
    return {"turns": serialized, "count": len(serialized)}


_HANDLERS = {
    "azurememory_search": _handle_search,
    "azurememory_list": _handle_list,
    "azurememory_recent": _handle_recent,
}


def handle_tool_call(
    client: Any,
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    thread_id: str,
    user_id: str,
) -> str:
    """Dispatch a tool call to the appropriate handler and return a JSON string."""
    args = args or {}
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"unknown tool: {tool_name}"})
    try:
        result = handler(client, args, thread_id=thread_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(result, ensure_ascii=False, default=str)
