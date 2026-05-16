import threading
from pathlib import Path

import pytest

from hermes_foundry_memory.storage import (
    append_entry,
    read_entries,
    write_entries_atomic,
)


def test_read_missing_returns_empty(tmp_path: Path):
    assert read_entries(tmp_path / "nope.md") == []


def test_read_empty_file(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("")
    assert read_entries(p) == []


def test_write_and_read_single(tmp_path: Path):
    p = tmp_path / "a.md"
    write_entries_atomic(p, ["hello"])
    assert read_entries(p) == ["hello"]
    assert p.read_text().endswith("\n")


def test_write_and_read_multiple(tmp_path: Path):
    p = tmp_path / "sub" / "a.md"
    entries = ["one", "two\nlines", "three"]
    write_entries_atomic(p, entries)
    assert read_entries(p) == entries


def test_round_trip(tmp_path: Path):
    p = tmp_path / "r.md"
    entries = ["a", "b", "c"]
    write_entries_atomic(p, entries)
    got = read_entries(p)
    write_entries_atomic(p, got)
    assert read_entries(p) == entries


def test_append_entry(tmp_path: Path):
    p = tmp_path / "ap.md"
    append_entry(p, "first")
    append_entry(p, "second")
    assert read_entries(p) == ["first", "second"]


def test_concurrent_writes_not_corrupted(tmp_path: Path):
    p = tmp_path / "c.md"
    payloads = [[f"writer-{i}-entry-{j}" for j in range(20)] for i in range(5)]

    def worker(entries):
        write_entries_atomic(p, entries)

    threads = [threading.Thread(target=worker, args=(pl,)) for pl in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = read_entries(p)
    assert result in payloads
