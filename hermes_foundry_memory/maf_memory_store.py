"""Native ``agent_framework.MemoryStore`` backend backed by Azure Blob Storage.

This module is an *optional* layer: it imports ``agent-framework-core``
(``import agent_framework``) and ``azure-storage-blob``. If
``agent-framework-core`` is not installed, the module is still safe to
import but :class:`AzureBlobMemoryStore` is replaced by a stub class that
raises :class:`RuntimeError` on construction (mirroring
:mod:`hermes_foundry_memory.maf_adapter`).

Blob layout (per ``source_id``)::

    {owner_id}/{source_id}/topics/{slug}.md   ← MemoryTopicRecord markdown
    {owner_id}/{source_id}/MEMORY.md          ← cached memory index
    {owner_id}/{source_id}/state.json         ← maintenance state
    {owner_id}/{source_id}/transcripts/...    ← raw transcript history (JSONL)

The store implements all 13 methods of ``af.MemoryStore`` against an
``azure.storage.blob.ContainerClient``. ``BlobServiceClient`` is
instantiated lazily on first use, mirroring ``client.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

# --- Optional Azure SDK imports ---------------------------------------------
try:  # pragma: no cover - exercised by env presence
    from azure.storage.blob import BlobServiceClient  # type: ignore
    from azure.core.exceptions import ResourceNotFoundError as _ResourceNotFoundError  # type: ignore

    _HAS_AZURE = True
except Exception:  # noqa: BLE001
    BlobServiceClient = None  # type: ignore[assignment]

    class _ResourceNotFoundError(Exception):  # type: ignore[no-redef]
        pass

    _HAS_AZURE = False


# --- Optional agent_framework imports ---------------------------------------
try:  # pragma: no cover
    import agent_framework  # noqa: F401
    from agent_framework._harness._memory import (  # type: ignore
        MemoryStore,
        MemoryTopicRecord,
        MemoryIndexEntry,
    )

    _HAS_MAF = True
except Exception:  # noqa: BLE001
    _HAS_MAF = False


# ---------------------------------------------------------------------------
# BlobPath helper
# ---------------------------------------------------------------------------
def _slugify(text: str) -> str:
    """Best-effort topic→slug mapping that matches MAF's ``_slugify_topic``.

    We try to delegate to MAF's helper when available so slugs match; the
    fallback is a simple lower/space→dash mapping (only exercised when MAF
    is not installed, which is also when the surrounding class is unusable).
    """
    if _HAS_MAF:
        from agent_framework._harness._memory import _slugify_topic  # type: ignore

        return _slugify_topic(text)
    cleaned = "".join(
        ch.lower() if ch.isalnum() else "-" for ch in text.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "_"


class BlobPath:
    """Map ``(owner_id, source_id, topic|index|state|transcript)`` to blob names.

    Path traversal segments (``..``, leading ``/``) are rejected so that
    a malicious ``owner_id`` or ``source_id`` cannot escape its prefix.
    """

    def __init__(self, *, owner_id: str, source_id: str) -> None:
        for label, value in (("owner_id", owner_id), ("source_id", source_id)):
            if not value or "/" in value or ".." in value.split("/"):
                raise ValueError(
                    f"{label} must not be empty or contain path-traversal segments."
                )
            for part in Path(value).parts:
                if part == ".." or part.startswith("/"):
                    raise ValueError(
                        f"{label} must not contain path-traversal segments."
                    )
        self.owner_id = owner_id
        self.source_id = source_id

    def _root(self) -> str:
        return f"{self.owner_id}/{self.source_id}"

    def topic(self, topic: str) -> str:
        return f"{self._root()}/topics/{_slugify(topic)}.md"

    def index(self) -> str:
        return f"{self._root()}/MEMORY.md"

    def state(self) -> str:
        return f"{self._root()}/state.json"

    def transcripts_prefix(self) -> str:
        return f"{self._root()}/transcripts/"


# ---------------------------------------------------------------------------
# AzureBlobMemoryStore
# ---------------------------------------------------------------------------
if _HAS_MAF:

    _DEFAULT_INDEX_HEADER = "# MEMORY"
    _DEFAULT_NO_TOPICS_TEXT = "- none yet"

    class AzureBlobMemoryStore(MemoryStore):  # type: ignore[misc]
        """``af.MemoryStore`` implementation backed by an Azure Blob container.

        The class is structured to mirror :class:`MemoryFileStore` (the local
        reference implementation that ships with ``agent-framework-core``)
        but persists each artifact as a single blob. ``put_blob`` is
        atomic on the Azure side, which is sufficient for our consistency
        needs (one writer per topic at a time, enforced by
        ``MemoryContextProvider``'s per-topic asyncio lock).
        """

        OWNER_STATE_KEY = "owner_id"

        def __init__(
            self,
            *,
            account_url: str,
            container: str,
            credential: Any | None = None,
            owner_id_strategy: str = "session",
            owner_state_key: str | None = None,
        ) -> None:
            """Initialize the blob-backed memory store.

            Keyword Args:
                account_url: ``https://{account}.blob.core.windows.net`` URL.
                container: Container name. Must already exist.
                credential: Any ``azure.identity`` credential. Defaults to
                    ``DefaultAzureCredential`` on first use when ``None``.
                owner_id_strategy: Currently only ``"session"`` is supported
                    — read ``session.state[owner_state_key]``.
                owner_state_key: Override for the session-state key holding
                    the logical owner id. Defaults to ``"owner_id"``.
            """
            self.account_url = account_url
            self.container = container
            self._credential = credential
            self.owner_id_strategy = owner_id_strategy
            self.owner_state_key = owner_state_key or self.OWNER_STATE_KEY
            self._service: Any | None = None
            self._container: Any | None = None

        # -- lazy clients ----------------------------------------------------
        def _service_client(self) -> Any:
            if self._service is None:
                if not _HAS_AZURE:
                    raise RuntimeError(
                        "azure-storage-blob is not installed. "
                        "Install it via the plugin's base dependencies."
                    )
                cred = self._credential
                if cred is None:
                    from azure.identity import DefaultAzureCredential  # type: ignore

                    cred = DefaultAzureCredential()
                self._service = BlobServiceClient(  # type: ignore[misc]
                    account_url=self.account_url, credential=cred
                )
            return self._service

        def _container_client(self) -> Any:
            if self._container is None:
                self._container = self._service_client().get_container_client(
                    self.container
                )
            return self._container

        # -- helpers ---------------------------------------------------------
        def _blob_path(self, session: Any, source_id: str) -> BlobPath:
            owner = self.get_owner_id(session)
            if owner is None:
                raise RuntimeError(
                    "AzureBlobMemoryStore requires "
                    f"session.state[{self.owner_state_key!r}] to be set."
                )
            return BlobPath(owner_id=owner, source_id=source_id)

        def _read_text(self, blob_name: str) -> str | None:
            client = self._container_client().get_blob_client(blob_name)
            try:
                stream = client.download_blob()
            except _ResourceNotFoundError:
                return None
            data = stream.readall()
            return data.decode("utf-8") if isinstance(data, bytes) else data

        def _write_text(self, blob_name: str, text: str) -> None:
            client = self._container_client().get_blob_client(blob_name)
            client.upload_blob(text.encode("utf-8"), overwrite=True)

        def _delete(self, blob_name: str) -> None:
            client = self._container_client().get_blob_client(blob_name)
            client.delete_blob()

        # -- core MemoryStore methods ---------------------------------------
        def get_owner_id(self, session: Any) -> str | None:
            value = None
            try:
                value = session.state.get(self.owner_state_key)
            except AttributeError:
                value = None
            return None if value is None else str(value)

        def export_provider_state(self, session: Any) -> dict[str, Any]:
            owner = self.get_owner_id(session)
            return {
                "owner_id": owner,
                "account_url": self.account_url,
                "container": self.container,
            }

        def import_provider_state(
            self, session: Any, *, state: Mapping[str, Any]
        ) -> None:
            owner = state.get("owner_id")
            if owner is not None:
                session.state[self.owner_state_key] = owner

        # ---- topics -------------------------------------------------------
        def list_topics(self, session: Any, *, source_id: str) -> list[Any]:
            bp = self._blob_path(session, source_id)
            prefix = f"{bp.owner_id}/{bp.source_id}/topics/"
            container = self._container_client()
            records: list[Any] = []
            for item in container.list_blobs(name_starts_with=prefix):
                name = item.name
                if not name.endswith(".md"):
                    continue
                text = self._read_text(name)
                if text is None:
                    continue
                stem = name.rsplit("/", 1)[-1][: -len(".md")]
                rec = MemoryTopicRecord.from_markdown(
                    text, fallback_topic=stem.replace("-", " ")
                )
                records.append(rec)
            return sorted(
                records, key=lambda r: (r.topic.lower(), r.updated_at)
            )

        def get_topic(
            self, session: Any, *, source_id: str, topic: str
        ) -> Any:
            bp = self._blob_path(session, source_id)
            text = self._read_text(bp.topic(topic))
            if text is None:
                raise FileNotFoundError(
                    f"No memory topic named '{topic}' was found for this owner."
                )
            return MemoryTopicRecord.from_markdown(text, fallback_topic=topic)

        def write_topic(
            self, session: Any, record: Any, *, source_id: str
        ) -> None:
            bp = self._blob_path(session, source_id)
            self._write_text(bp.topic(record.slug), record.to_markdown() + "\n")

        def delete_topic(
            self, session: Any, *, source_id: str, topic: str
        ) -> None:
            bp = self._blob_path(session, source_id)
            try:
                self._delete(bp.topic(topic))
            except _ResourceNotFoundError as exc:
                raise FileNotFoundError(
                    f"No memory topic named '{topic}' was found for this owner."
                ) from exc

        # ---- state --------------------------------------------------------
        @staticmethod
        def _default_state() -> dict[str, Any]:
            return {
                "last_consolidated_at": None,
                "sessions_since_consolidation": [],
            }

        def read_state(self, session: Any, *, source_id: str) -> dict[str, Any]:
            bp = self._blob_path(session, source_id)
            text = self._read_text(bp.state())
            if text is None:
                return self._default_state()
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("Memory state file must contain a JSON object.")
            merged = {**self._default_state(), **raw}
            if not isinstance(merged.get("sessions_since_consolidation"), list):
                merged["sessions_since_consolidation"] = []
            if not isinstance(
                merged.get("last_consolidated_at"), (str, type(None))
            ):
                merged["last_consolidated_at"] = None
            return merged

        def write_state(
            self, session: Any, state: Mapping[str, Any], *, source_id: str
        ) -> None:
            bp = self._blob_path(session, source_id)
            self._write_text(bp.state(), json.dumps(dict(state)) + "\n")

        # ---- index --------------------------------------------------------
        def _render_index(
            self,
            entries: Sequence[Any],
            *,
            line_limit: int,
            line_length: int,
        ) -> str:
            pointer_lines = [
                e.to_pointer_line(max_length=line_length)
                for e in entries[:line_limit]
            ]
            lines = [
                _DEFAULT_INDEX_HEADER,
                "",
                *(pointer_lines if pointer_lines else [_DEFAULT_NO_TOPICS_TEXT]),
            ]
            return "\n".join(lines).rstrip()

        def rebuild_index(
            self,
            session: Any,
            *,
            source_id: str,
            line_limit: int,
            line_length: int,
        ) -> list[Any]:
            topics = self.list_topics(session, source_id=source_id)
            entries = [MemoryIndexEntry.from_topic_record(t) for t in topics]
            text = self._render_index(
                entries, line_limit=line_limit, line_length=line_length
            )
            bp = self._blob_path(session, source_id)
            self._write_text(bp.index(), text + "\n")
            return entries[:line_limit]

        def get_index_text(
            self,
            session: Any,
            *,
            source_id: str,
            line_limit: int,
            line_length: int,
            index_entries: Sequence[Any] | None = None,
        ) -> str:
            bp = self._blob_path(session, source_id)
            if index_entries is None:
                self.rebuild_index(
                    session,
                    source_id=source_id,
                    line_limit=line_limit,
                    line_length=line_length,
                )
            else:
                text = self._render_index(
                    index_entries,
                    line_limit=line_limit,
                    line_length=line_length,
                )
                self._write_text(bp.index(), text + "\n")
            cur = self._read_text(bp.index()) or ""
            return cur.strip()

        # ---- transcripts --------------------------------------------------
        # TODO: ``get_transcripts_directory`` returns a *local* cache path
        # because ``af.MemoryStore`` requires a ``Path``. Transcripts written
        # there are NOT yet synced to blob storage; a future
        # ``sync_transcripts`` method will upload completed JSONL files to
        # ``{owner}/{source}/transcripts/``. ``search_transcripts`` queries
        # the blob side directly so search works in cross-host deployments.
        def get_transcripts_directory(
            self, session: Any, *, source_id: str
        ) -> Path:
            bp = self._blob_path(session, source_id)
            base = (
                Path("~/.cache/hermes-foundry-memory/transcripts")
                / bp.owner_id
                / bp.source_id
            ).expanduser()
            base.mkdir(parents=True, exist_ok=True)
            return base

        def search_transcripts(
            self,
            session: Any,
            *,
            source_id: str,
            query: str,
            session_id: str | None = None,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            # TODO: replace this naive substring scan with an Azure AI Search
            # index over the transcript blobs once the sync pipeline lands.
            normalized = query.strip()
            if not normalized:
                raise ValueError("query must not be empty.")
            needle = normalized.casefold()
            bp = self._blob_path(session, source_id)
            prefix = bp.transcripts_prefix()
            container = self._container_client()
            results: list[dict[str, Any]] = []
            for item in container.list_blobs(name_starts_with=prefix):
                name = item.name
                if session_id is not None and session_id not in name:
                    continue
                text = self._read_text(name) or ""
                for line_no, raw_line in enumerate(text.splitlines(), start=1):
                    if not raw_line.strip():
                        continue
                    snippet = raw_line
                    try:
                        payload = json.loads(raw_line)
                        if isinstance(payload, dict):
                            # Try to extract the textual content from common
                            # ``Message``-shaped payloads.
                            extracted = _extract_text_from_payload(payload)
                            if extracted:
                                snippet = extracted
                    except json.JSONDecodeError:
                        pass
                    if needle not in snippet.casefold():
                        continue
                    results.append(
                        {
                            "blob": name,
                            "line_number": line_no,
                            "snippet": snippet,
                            "score": 1.0,
                        }
                    )
                    if len(results) >= limit:
                        return results
            return results

    def _extract_text_from_payload(payload: dict) -> str:
        """Best-effort ``Message``-payload → text extraction for search."""
        if isinstance(payload.get("text"), str):
            return payload["text"]
        contents = payload.get("contents")
        if isinstance(contents, list):
            parts: list[str] = []
            for c in contents:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            if parts:
                return "\n".join(parts)
        return ""

else:  # pragma: no cover - executed only when MAF is missing

    class AzureBlobMemoryStore:  # type: ignore[no-redef]
        """Stub raised when ``agent-framework-core`` is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(
                "agent-framework-core is not installed. "
                "Install the optional extra: pip install "
                "'hermes-foundry-memory-plugin[maf]'"
            )


__all__ = ["AzureBlobMemoryStore", "BlobPath"]
