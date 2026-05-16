"""Microsoft Agent Framework (MAF) adapter for Hermes Foundry Memory plugin.

This module is an *optional* layer: it imports the
``agent-framework-core`` package (``import agent_framework as af``) and
exposes the three Foundry memory operations (``search`` / ``list`` /
``recent``) as MAF ``FunctionTool`` instances that can be passed straight
into an ``af.ChatAgent``.

The underlying business logic in :mod:`hermes_foundry_memory.tools` and
:mod:`hermes_foundry_memory.client` is left untouched — this is a *thin*
wrapper layer.

If ``agent-framework-core`` is not installed, importing this module is
still safe, but :func:`get_maf_tools` will raise :class:`RuntimeError`.
"""
from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised via importorskip in tests
    import agent_framework as af  # type: ignore

    _HAS_MAF = True
except Exception:  # noqa: BLE001
    af = None  # type: ignore[assignment]
    _HAS_MAF = False


from .tools import _serialize  # reuse the existing dataclass serializer


def _require_maf() -> None:
    if not _HAS_MAF:
        raise RuntimeError(
            "agent-framework-core is not installed. "
            "Install the optional extra: pip install "
            "'hermes-foundry-memory-plugin[maf]'"
        )


def get_maf_tools(
    *,
    client: Any,
    thread_id: str,
    user_id: str,
) -> list[Any]:
    """Build MAF ``FunctionTool`` wrappers bound to ``client``.

    Args:
        client: A :class:`hermes_foundry_memory.client.FoundryClient`
            instance (or any duck-typed equivalent — useful for tests).
        thread_id: Foundry thread id used by the ``foundry_recent`` tool.
        user_id: Default scope for long-term search/list operations.

    Returns:
        A list of three :class:`agent_framework.FunctionTool` instances
        named ``foundry_search``, ``foundry_list`` and ``foundry_recent``.

    Raises:
        RuntimeError: If ``agent-framework-core`` is not installed.
    """
    _require_maf()

    @af.tool(  # type: ignore[union-attr]
        name="foundry_search",
        description=(
            "Semantic / hybrid search over the user's long-term Azure AI "
            "Foundry memory store. Returns the top matching memory records "
            "for the given natural-language query."
        ),
    )
    def foundry_search(query: str, top_k: int = 10) -> list[dict]:
        """Search long-term Foundry memory.

        Args:
            query: Natural-language query.
            top_k: Maximum number of results (clamped 1..20).
        """
        k = max(1, min(20, int(top_k)))
        results = client.search_long_term(
            query=query, scope=user_id, namespace=None, top_k=k
        )
        return [_serialize(r) for r in results]

    @af.tool(  # type: ignore[union-attr]
        name="foundry_list",
        description=(
            "List recent long-term Foundry memory records for the user, "
            "without a search query. Useful for browsing what is stored."
        ),
    )
    def foundry_list(max_results: int = 20) -> list[dict]:
        """List long-term records for the bound user.

        Args:
            max_results: Maximum number of records (clamped 1..100).
        """
        n = max(1, min(100, int(max_results)))
        results = client.list_long_term(
            scope=user_id, namespace=None, max_results=n
        )
        return [_serialize(r) for r in results]

    @af.tool(  # type: ignore[union-attr]
        name="foundry_recent",
        description=(
            "Fetch the last k turns from the current Foundry conversation "
            "thread (short-term memory)."
        ),
    )
    def foundry_recent(k: int = 5) -> list[dict]:
        """Return the last *k* messages of the bound thread.

        Args:
            k: Number of recent turns (clamped 1..20).
        """
        n = max(1, min(20, int(k)))
        turns = client.get_last_k_turns(thread_id=thread_id, k=n)
        out: list[dict] = []
        for t in turns:
            d = _serialize(t)
            if isinstance(d, dict):
                out.append(
                    {"role": d.get("role"), "content": d.get("content")}
                )
            else:
                out.append(
                    {
                        "role": getattr(t, "role", None),
                        "content": getattr(t, "content", None),
                    }
                )
        return out

    return [foundry_search, foundry_list, foundry_recent]


__all__ = ["get_maf_tools"]
