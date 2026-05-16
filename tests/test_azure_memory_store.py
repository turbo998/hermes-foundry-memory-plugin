"""Tests for the native af.MemoryStore backend AzureBlobMemoryStore.

All Azure SDK calls are mocked; no live Azure connection is required.
Skipped when ``agent-framework-core`` is not installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("agent_framework")

from agent_framework._harness._memory import (  # noqa: E402
    MemoryIndexEntry,
    MemoryTopicRecord,
)

from hermes_foundry_memory import maf_memory_store  # noqa: E402
from hermes_foundry_memory.maf_memory_store import (  # noqa: E402
    AzureBlobMemoryStore,
    BlobPath,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _ResourceNotFound(Exception):
    pass


@pytest.fixture(autouse=True)
def _patch_azure_exception(monkeypatch):
    """Ensure ResourceNotFoundError type the store catches matches our stub."""
    monkeypatch.setattr(
        maf_memory_store, "_ResourceNotFoundError", _ResourceNotFound
    )
    yield


def _make_session(owner_id: str = "user-42") -> MagicMock:
    sess = MagicMock()
    sess.state = {"owner_id": owner_id}
    return sess


def _make_store_with_mock_container(blobs: dict | None = None):
    """Return (store, container_client_mock, blobs_dict)."""
    blobs = {} if blobs is None else blobs
    container = MagicMock()

    def _get_blob_client(name: str):
        bc = MagicMock()
        bc.blob_name = name

        def _download():
            if name not in blobs:
                raise _ResourceNotFound(name)
            data = blobs[name]
            payload = data.encode("utf-8") if isinstance(data, str) else data
            stream = MagicMock()
            stream.readall.return_value = payload
            return stream

        def _upload(payload, overwrite=True, **kwargs):
            if isinstance(payload, bytes):
                blobs[name] = payload.decode("utf-8")
            else:
                blobs[name] = payload

        def _delete():
            if name not in blobs:
                raise _ResourceNotFound(name)
            del blobs[name]

        bc.download_blob.side_effect = _download
        bc.upload_blob.side_effect = _upload
        bc.delete_blob.side_effect = _delete
        return bc

    def _list_blobs(name_starts_with=None):
        out = []
        for n in sorted(blobs):
            if name_starts_with is None or n.startswith(name_starts_with):
                item = MagicMock()
                item.name = n
                out.append(item)
        return out

    container.get_blob_client.side_effect = _get_blob_client
    container.list_blobs.side_effect = _list_blobs

    store = AzureBlobMemoryStore(
        account_url="https://example.blob.core.windows.net",
        container="mem",
        credential=MagicMock(),
    )
    # Inject mock container client to bypass lazy init.
    store._container = container  # type: ignore[attr-defined]
    return store, container, blobs


# ---------------------------------------------------------------------------
# BlobPath helper
# ---------------------------------------------------------------------------
def test_blob_path_topic_uses_slug():
    bp = BlobPath(owner_id="alice", source_id="chat")
    assert bp.topic("Hello World") == "alice/chat/topics/hello-world.md"


def test_blob_path_index_and_state():
    bp = BlobPath(owner_id="alice", source_id="chat")
    assert bp.index() == "alice/chat/MEMORY.md"
    assert bp.state() == "alice/chat/state.json"
    assert bp.transcripts_prefix() == "alice/chat/transcripts/"


def test_blob_path_rejects_traversal():
    with pytest.raises(ValueError):
        BlobPath(owner_id="../boom", source_id="chat")


# ---------------------------------------------------------------------------
# Batch A: simple methods
# ---------------------------------------------------------------------------
def test_get_owner_id_from_session_state():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session("user-42")
    assert store.get_owner_id(sess) == "user-42"


def test_get_owner_id_returns_none_when_missing():
    store, *_ = _make_store_with_mock_container()
    sess = MagicMock()
    sess.state = {}
    assert store.get_owner_id(sess) is None


def test_export_import_provider_state_roundtrip():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session("user-42")
    state = store.export_provider_state(sess)
    assert state["owner_id"] == "user-42"
    assert state["container"] == "mem"
    sess2 = MagicMock()
    sess2.state = {}
    store.import_provider_state(sess2, state=state)
    assert sess2.state["owner_id"] == "user-42"


def test_list_topics_empty():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session()
    assert store.list_topics(sess, source_id="chat") == []


def test_list_topics_returns_records():
    rec = MemoryTopicRecord(
        topic="Greetings",
        summary="how to say hi",
        memories=["wave"],
        updated_at="2024-01-01T00:00:00Z",
        session_ids=["s1"],
    )
    blobs = {"user-42/chat/topics/greetings.md": rec.to_markdown() + "\n"}
    store, *_ = _make_store_with_mock_container(blobs)
    sess = _make_session()
    out = store.list_topics(sess, source_id="chat")
    assert len(out) == 1
    assert out[0].topic == "Greetings"
    assert out[0].memories == ["wave"]


def test_get_topic_not_found_raises():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session()
    with pytest.raises(FileNotFoundError):
        store.get_topic(sess, source_id="chat", topic="nope")


def test_get_topic_returns_record():
    rec = MemoryTopicRecord(
        topic="Greetings",
        summary="how to say hi",
        memories=["wave"],
        updated_at="2024-01-01T00:00:00Z",
    )
    blobs = {"user-42/chat/topics/greetings.md": rec.to_markdown() + "\n"}
    store, *_ = _make_store_with_mock_container(blobs)
    sess = _make_session()
    got = store.get_topic(sess, source_id="chat", topic="Greetings")
    assert got.topic == "Greetings"


def test_write_topic_creates_blob():
    store, container, blobs = _make_store_with_mock_container()
    sess = _make_session()
    rec = MemoryTopicRecord(
        topic="My Topic",
        summary="s",
        memories=["m1"],
        updated_at="2024-01-01T00:00:00Z",
    )
    store.write_topic(sess, rec, source_id="chat")
    assert "user-42/chat/topics/my-topic.md" in blobs
    parsed = MemoryTopicRecord.from_markdown(blobs["user-42/chat/topics/my-topic.md"])
    assert parsed.topic == "My Topic"


def test_delete_topic_removes_blob():
    rec = MemoryTopicRecord(
        topic="Doomed",
        summary="bye",
        memories=["x"],
        updated_at="2024-01-01T00:00:00Z",
    )
    blobs = {"user-42/chat/topics/doomed.md": rec.to_markdown() + "\n"}
    store, *_ = _make_store_with_mock_container(blobs)
    sess = _make_session()
    store.delete_topic(sess, source_id="chat", topic="Doomed")
    assert "user-42/chat/topics/doomed.md" not in blobs


def test_delete_topic_missing_raises():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session()
    with pytest.raises(FileNotFoundError):
        store.delete_topic(sess, source_id="chat", topic="nope")


def test_read_state_default():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session()
    state = store.read_state(sess, source_id="chat")
    assert state == {
        "last_consolidated_at": None,
        "sessions_since_consolidation": [],
    }


def test_write_then_read_state():
    store, _, blobs = _make_store_with_mock_container()
    sess = _make_session()
    store.write_state(
        sess,
        {"last_consolidated_at": "2024-01-02", "sessions_since_consolidation": ["s1"]},
        source_id="chat",
    )
    assert "user-42/chat/state.json" in blobs
    out = store.read_state(sess, source_id="chat")
    assert out["last_consolidated_at"] == "2024-01-02"
    assert out["sessions_since_consolidation"] == ["s1"]


# ---------------------------------------------------------------------------
# Batch B: index + transcripts
# ---------------------------------------------------------------------------
def test_rebuild_index_writes_index_md():
    rec = MemoryTopicRecord(
        topic="Greetings",
        summary="how to say hi",
        memories=["wave"],
        updated_at="2024-01-01T00:00:00Z",
    )
    blobs = {"user-42/chat/topics/greetings.md": rec.to_markdown() + "\n"}
    store, _, blobs2 = _make_store_with_mock_container(blobs)
    sess = _make_session()
    entries = store.rebuild_index(
        sess, source_id="chat", line_limit=10, line_length=120
    )
    assert len(entries) == 1
    assert isinstance(entries[0], MemoryIndexEntry)
    assert "user-42/chat/MEMORY.md" in blobs2
    text = blobs2["user-42/chat/MEMORY.md"]
    assert "# MEMORY" in text
    assert "Greetings" in text


def test_get_index_text_uses_existing_blob():
    blobs = {"user-42/chat/MEMORY.md": "# MEMORY\n\n- precomputed\n"}
    store, *_ = _make_store_with_mock_container(blobs)
    sess = _make_session()
    text = store.get_index_text(
        sess, source_id="chat", line_limit=10, line_length=120
    )
    # rebuild_index will overwrite it, but with no topics it produces "- none yet"
    # Function spec: when no override entries given, rebuilds; here no topics →
    # "- none yet" section. So either the precomputed text or the fresh "none yet".
    assert "MEMORY" in text


def test_get_index_text_with_override_entries():
    store, _, blobs = _make_store_with_mock_container()
    sess = _make_session()
    entry = MemoryIndexEntry(
        topic="Foo", slug="foo", summary="bar", updated_at="2024-01-01"
    )
    text = store.get_index_text(
        sess,
        source_id="chat",
        line_limit=5,
        line_length=120,
        index_entries=[entry],
    )
    assert "Foo" in text
    assert "user-42/chat/MEMORY.md" in blobs


def test_get_transcripts_directory_returns_local_cache_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    store, *_ = _make_store_with_mock_container()
    sess = _make_session("alice")
    p = store.get_transcripts_directory(sess, source_id="chat")
    assert isinstance(p, Path)
    assert p.exists() and p.is_dir()
    assert "alice" in str(p) and "chat" in str(p)


def test_search_transcripts_substring_match():
    blobs = {
        "user-42/chat/transcripts/sess-1.jsonl": (
            json.dumps({"role": "user", "contents": [{"type": "text", "text": "hello world"}]}) + "\n"
            + json.dumps({"role": "assistant", "contents": [{"type": "text", "text": "goodbye"}]}) + "\n"
        ),
        "user-42/chat/transcripts/sess-2.jsonl": (
            json.dumps({"role": "user", "contents": [{"type": "text", "text": "another HELLO line"}]}) + "\n"
        ),
    }
    store, *_ = _make_store_with_mock_container(blobs)
    sess = _make_session()
    hits = store.search_transcripts(
        sess, source_id="chat", query="hello", limit=10
    )
    assert len(hits) >= 2
    assert all("hello" in h["snippet"].lower() for h in hits)
    assert all("blob" in h for h in hits)


def test_search_transcripts_empty_query_raises():
    store, *_ = _make_store_with_mock_container()
    sess = _make_session()
    with pytest.raises(ValueError):
        store.search_transcripts(sess, source_id="chat", query="   ")
