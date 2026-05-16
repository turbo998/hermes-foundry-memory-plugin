"""FoundryMemoryProvider — primary-mode sync + hooks."""
from __future__ import annotations

import fcntl
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import tools
from .breaker import CircuitBreaker
from .client import FoundryClient, Message
from .storage import read_entries, write_entries_atomic

logger = logging.getLogger(__name__)

FEATURES = [
    "semantic_search",
    "user_preferences",
    "conversation_summaries",
    "episodic_memory",
    "ha_backup",
    "primary_storage",
]

_PRIMARY_NS = "/builtin-primary"
_BACKUP_NS = "/builtin-backup"


class FoundryMemoryProvider:
    name = "foundry_memory"
    features = list(FEATURES)

    def __init__(
        self,
        client: Optional[FoundryClient] = None,
        config: Optional[dict[str, Any]] = None,
        hermes_home: Optional[Path] = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        if client is None:
            endpoint = self.config.get("foundry_endpoint")
            if endpoint:
                from .client import AzureFoundryClient

                client = AzureFoundryClient(
                    endpoint=endpoint,
                    memory_store_name=self.config.get(
                        "memory_store_name", "hermes_user_mem"
                    ),
                )
            else:
                raise ValueError(
                    "FoundryMemoryProvider requires either a client or "
                    "config.foundry_endpoint"
                )
        self.client: FoundryClient = client

        self.hermes_home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
        self.memories_dir = self.hermes_home / "memories"
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.memories_dir / "MEMORY.md"
        self.user_file = self.memories_dir / "USER.md"

        self.session_id: str = ""
        self.user_id: str = self.config.get("user_id", "default-user")

        self._breaker = CircuitBreaker()
        self._sync_queue: queue.Queue = queue.Queue()
        self._shutdown = threading.Event()
        self._prefetch_cache: dict[str, list] = {}
        self._prefetch_order: list[str] = []

        self._worker = threading.Thread(
            target=self._worker_loop, name="foundry-sync", daemon=True
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initialize(
        self, session_id: str, user_id: str, agent_context: str = ""
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id or self.user_id

        if not self.config.get("primary_mode", True):
            return
        if agent_context not in {"primary", "flush"}:
            return

        # acquire a non-blocking lock to prevent concurrent pulls
        lock_path = self.memories_dir / ".pull.lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
        except OSError as exc:
            logger.warning("could not open pull lock: %s", exc)
            return
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.info("primary pull lock held by another process; skipping")
                return
            try:
                self._pull_namespace_to_file(
                    f"{_PRIMARY_NS}/memory/", self.memory_file
                )
                self._pull_namespace_to_file(
                    f"{_PRIMARY_NS}/user/", self.user_file
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _pull_namespace_to_file(self, namespace: str, path: Path) -> None:
        try:
            records = self.client.list_long_term(
                scope=self.user_id, namespace=namespace, max_results=0
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to pull %s: %s", namespace, exc)
            self._breaker.record_failure()
            return
        entries = [r.content for r in records if getattr(r, "content", None)]
        if entries:
            write_entries_atomic(path, entries)
        self._breaker.record_success()

    def shutdown(self) -> None:
        self._shutdown.set()
        try:
            self._sync_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        self._worker.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Local memory writes + queue cloud sync
    # ------------------------------------------------------------------
    def _target_path(self, target: str) -> Path:
        if target == "memory":
            return self.memory_file
        if target == "user":
            return self.user_file
        raise ValueError(f"unknown memory target: {target}")

    def on_memory_write(
        self, target: str, action: str, content: str, old_text: str = ""
    ) -> None:
        path = self._target_path(target)
        entries = read_entries(path)
        if action == "add":
            entries.append(content)
        elif action == "replace":
            entries = [content if e == old_text else e for e in entries]
        elif action == "remove":
            entries = [e for e in entries if e != old_text]
        else:
            raise ValueError(f"unknown action: {action}")
        write_entries_atomic(path, entries)

        ns = f"{_PRIMARY_NS}/{target}/"
        self._enqueue(
            {
                "op": "memory_sync",
                "payload": {
                    "scope": self.user_id,
                    "namespace": ns,
                    "entries": list(entries),
                },
            }
        )
        if self.config.get("ha_backup"):
            self._enqueue(
                {
                    "op": "memory_sync",
                    "payload": {
                        "scope": self.user_id,
                        "namespace": f"{_BACKUP_NS}/{target}/",
                        "entries": list(entries),
                    },
                }
            )

    def sync_turn(self, user_msg: str, assistant_msg: str) -> None:
        self._enqueue(
            {
                "op": "add_turns",
                "payload": {
                    "thread_id": self.session_id,
                    "user_id": self.user_id,
                    "messages": [
                        Message(role="user", content=user_msg),
                        Message(role="assistant", content=assistant_msg),
                    ],
                },
            }
        )

    def on_pre_compress(self, messages: list[dict]) -> None:
        last = messages[-10:]
        joined = "\n".join(
            f"[{m.get('role', 'OTHER')}] {m.get('content', '')}" for m in last
        )
        self._enqueue(
            {
                "op": "batch_create",
                "payload": {
                    "scope": self.user_id,
                    "namespace": "/compressed/",
                    "contents": [joined],
                },
            }
        )

    def on_delegation(self, task: str, result: str, child_session_id: str) -> None:
        contents = [
            f"task: {task}",
            f"result: {result}",
            f"child_session_id: {child_session_id}",
        ]
        self._enqueue(
            {
                "op": "batch_create",
                "payload": {
                    "scope": self.user_id,
                    "namespace": "/delegation/",
                    "contents": contents,
                },
            }
        )

    # ------------------------------------------------------------------
    # Prefetch
    # ------------------------------------------------------------------
    def queue_prefetch(self, query: str) -> None:
        # async enqueue (no-op worker job; primarily for instrumentation)
        self._enqueue({"op": "prefetch", "payload": {"query": query}})
        try:
            results = self.client.search_long_term(
                query=query,
                scope=self.user_id,
                namespace=f"{_PRIMARY_NS}/memory/",
                top_k=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("prefetch failed: %s", exc)
            self._breaker.record_failure()
            return
        self._prefetch_cache[query] = list(results)
        if query in self._prefetch_order:
            self._prefetch_order.remove(query)
        self._prefetch_order.append(query)

    def get_prefetch_block(self) -> str:
        if not self._prefetch_order:
            return ""
        parts: list[str] = []
        for q in self._prefetch_order:
            results = self._prefetch_cache.get(q, [])
            if not results:
                continue
            parts.append(f"### prefetch: {q}")
            for r in results:
                parts.append(f"- {getattr(r, 'content', str(r))}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # System prompt + tools
    # ------------------------------------------------------------------
    def system_prompt_block(self) -> str:
        chunks: list[str] = []
        mem = read_entries(self.memory_file)
        if mem:
            chunks.append("## MEMORY\n" + "\n".join(f"- {e}" for e in mem))
        usr = read_entries(self.user_file)
        if usr:
            chunks.append("## USER\n" + "\n".join(f"- {e}" for e in usr))
        pf = self.get_prefetch_block()
        if pf:
            chunks.append(pf)
        return "\n\n".join(chunks)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return tools.get_tool_schemas()

    def handle_tool_call(self, name: str, args: dict[str, Any] | None) -> str:
        return tools.handle_tool_call(
            self.client,
            name,
            args,
            thread_id=self.session_id,
            user_id=self.user_id,
        )

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _enqueue(self, task: dict[str, Any]) -> None:
        self._sync_queue.put(task)

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._sync_queue.get(timeout=0.1)
            except queue.Empty:
                if self._shutdown.is_set():
                    return
                continue
            try:
                if task is None:
                    return
                if self._breaker.is_open():
                    logger.debug("breaker open; skipping task %s", task.get("op"))
                    continue
                try:
                    self._dispatch(task)
                    self._breaker.record_success()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("sync task failed: %s", exc)
                    self._breaker.record_failure()
            finally:
                self._sync_queue.task_done()

    def _dispatch(self, task: dict[str, Any]) -> None:
        op = task.get("op")
        payload = task.get("payload", {})
        if op == "memory_sync":
            self.client.batch_create_records(
                scope=payload["scope"],
                namespace=payload["namespace"],
                contents=payload["entries"],
            )
        elif op == "add_turns":
            self.client.add_turns(
                thread_id=payload["thread_id"],
                user_id=payload["user_id"],
                messages=payload["messages"],
            )
        elif op == "batch_create":
            self.client.batch_create_records(
                scope=payload["scope"],
                namespace=payload["namespace"],
                contents=payload["contents"],
            )
        elif op == "prefetch":
            # already executed synchronously in queue_prefetch
            pass
        else:
            logger.warning("unknown sync op: %s", op)

    def _wait_idle(self, timeout: float = 2.0) -> bool:
        """Block until the sync queue drains or *timeout* expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._sync_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False


__all__ = ["FoundryMemoryProvider"]
