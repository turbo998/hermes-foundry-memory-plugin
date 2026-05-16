"""Foundry client abstraction.

Provides:
    * :class:`Message`, :class:`MemoryRecord` - lightweight data carriers.
    * :class:`FoundryClient` - the abstract interface plugin code depends on.
    * :class:`MockFoundryClient` - in-memory implementation for tests and
      offline integration runs.
    * :class:`AzureFoundryClient` - thin adapter onto the Azure AI Projects
      preview SDK.  Most behaviour is currently best-effort and will raise
      :class:`NotImplementedError` until a live Foundry environment is wired
      up.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------
@dataclass
class Message:
    """A single chat turn."""

    role: str
    content: str
    timestamp: Optional[str] = None


@dataclass
class MemoryRecord:
    """A long-term memory record returned by Foundry's memory store."""

    content: str
    strategy: Optional[str] = None
    namespace: Optional[str] = None


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------
class FoundryClient(abc.ABC):
    """Abstract interface to the Azure Foundry memory + threads APIs."""

    @abc.abstractmethod
    def add_turns(
        self,
        thread_id: str,
        user_id: str,
        messages: Sequence[Message],
    ) -> None:
        """Append ``messages`` to a Foundry thread."""

    @abc.abstractmethod
    def search_long_term(
        self,
        query: str,
        scope: str,
        namespace: str,
        top_k: int,
    ) -> List[MemoryRecord]:
        """Semantic search over a long-term memory namespace."""

    @abc.abstractmethod
    def list_long_term(
        self,
        scope: str,
        namespace: str,
        max_results: int,
    ) -> List[MemoryRecord]:
        """List recent records in a namespace (no semantic ranking)."""

    @abc.abstractmethod
    def get_last_k_turns(self, thread_id: str, k: int) -> List[Message]:
        """Return the most recent ``k`` messages from ``thread_id``."""

    @abc.abstractmethod
    def batch_create_records(
        self,
        scope: str,
        namespace: str,
        contents: Sequence[str],
    ) -> None:
        """Write a batch of memory records into a namespace."""

    @abc.abstractmethod
    def delete_namespace(self, scope: str, namespace: str) -> None:
        """Remove every record in a namespace (best-effort)."""


# ---------------------------------------------------------------------------
# Mock implementation
# ---------------------------------------------------------------------------
@dataclass
class MockFoundryClient(FoundryClient):
    """In-memory FoundryClient used by unit + offline integration tests."""

    fail_writes: bool = False
    fail_reads: bool = False

    threads: Dict[str, List[Message]] = field(default_factory=dict)
    records: Dict[Tuple[str, str], List[MemoryRecord]] = field(default_factory=dict)
    preset_search_results: List[MemoryRecord] = field(default_factory=list)
    calls: List[Tuple] = field(default_factory=list)

    # -- threads --------------------------------------------------------
    def add_turns(
        self,
        thread_id: str,
        user_id: str,
        messages: Sequence[Message],
    ) -> None:
        self.calls.append(("add_turns", thread_id, user_id, list(messages)))
        if self.fail_writes:
            raise RuntimeError("mock: fail_writes=True")
        self.threads.setdefault(thread_id, []).extend(messages)

    def get_last_k_turns(self, thread_id: str, k: int) -> List[Message]:
        self.calls.append(("get_last_k_turns", thread_id, k))
        if self.fail_reads:
            raise RuntimeError("mock: fail_reads=True")
        turns = self.threads.get(thread_id, [])
        if k <= 0:
            return []
        return list(turns[-k:])

    # -- long-term memory ----------------------------------------------
    def search_long_term(
        self,
        query: str,
        scope: str,
        namespace: str,
        top_k: int,
    ) -> List[MemoryRecord]:
        self.calls.append(("search_long_term", query, scope, namespace, top_k))
        if self.fail_reads:
            raise RuntimeError("mock: fail_reads=True")
        return list(self.preset_search_results[:top_k]) if top_k else list(
            self.preset_search_results
        )

    def list_long_term(
        self,
        scope: str,
        namespace: str,
        max_results: int,
    ) -> List[MemoryRecord]:
        self.calls.append(("list_long_term", scope, namespace, max_results))
        if self.fail_reads:
            raise RuntimeError("mock: fail_reads=True")
        recs = self.records.get((scope, namespace), [])
        return list(recs[:max_results]) if max_results else list(recs)

    def batch_create_records(
        self,
        scope: str,
        namespace: str,
        contents: Sequence[str],
    ) -> None:
        self.calls.append(("batch_create_records", scope, namespace, list(contents)))
        if self.fail_writes:
            raise RuntimeError("mock: fail_writes=True")
        bucket = self.records.setdefault((scope, namespace), [])
        for c in contents:
            bucket.append(MemoryRecord(content=c, namespace=namespace))

    def delete_namespace(self, scope: str, namespace: str) -> None:
        self.calls.append(("delete_namespace", scope, namespace))
        if self.fail_writes:
            raise RuntimeError("mock: fail_writes=True")
        self.records.pop((scope, namespace), None)


# ---------------------------------------------------------------------------
# Azure implementation (stub)
# ---------------------------------------------------------------------------
class AzureFoundryClient(FoundryClient):
    """Thin adapter onto Azure AI Projects + memory-store preview APIs.

    Construction is cheap and only resolves a credential lazily.  Each method
    is currently a stub that raises :class:`NotImplementedError` so callers
    can wire the plugin against the real service incrementally.
    """

    def __init__(
        self,
        endpoint: str,
        memory_store_name: str,
        credential: object = None,
    ) -> None:
        self.endpoint = endpoint
        self.memory_store_name = memory_store_name
        self._credential = credential
        self._project = None  # lazy

    # -- credential / client construction ------------------------------
    def _get_credential(self):
        if self._credential is not None:
            return self._credential
        try:
            from azure.identity.aio import DefaultAzureCredential  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised in live env
            raise ImportError(
                "azure-identity is required for AzureFoundryClient; "
                "install it or pass an explicit credential"
            ) from exc
        self._credential = DefaultAzureCredential()
        return self._credential

    def _get_project(self):
        if self._project is not None:
            return self._project
        try:
            from azure.ai.projects.aio import AIProjectClient  # type: ignore
        except ImportError as exc:  # pragma: no cover - live only
            raise ImportError(
                "azure-ai-projects is required for AzureFoundryClient"
            ) from exc
        self._project = AIProjectClient(
            endpoint=self.endpoint, credential=self._get_credential()
        )
        return self._project

    # -- FoundryClient interface --------------------------------------
    def add_turns(
        self,
        thread_id: str,
        user_id: str,
        messages: Sequence[Message],
    ) -> None:
        raise NotImplementedError("requires live Azure Foundry")

    def search_long_term(
        self,
        query: str,
        scope: str,
        namespace: str,
        top_k: int,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("requires live Azure Foundry")

    def list_long_term(
        self,
        scope: str,
        namespace: str,
        max_results: int,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("requires live Azure Foundry")

    def get_last_k_turns(self, thread_id: str, k: int) -> List[Message]:
        raise NotImplementedError("requires live Azure Foundry")

    def batch_create_records(
        self,
        scope: str,
        namespace: str,
        contents: Sequence[str],
    ) -> None:
        raise NotImplementedError("requires live Azure Foundry")

    def delete_namespace(self, scope: str, namespace: str) -> None:
        raise NotImplementedError("requires live Azure Foundry")


__all__ = [
    "Message",
    "MemoryRecord",
    "FoundryClient",
    "MockFoundryClient",
    "AzureFoundryClient",
]
