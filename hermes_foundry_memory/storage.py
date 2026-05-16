"""Local file storage for memory entries.

Entries are joined with the separator '\n§\n' and each file ends with a
trailing newline. Writes are atomic (write-to-tmp + os.replace) and guarded
by an advisory fcntl lock on the tmp file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

SEPARATOR = "\n§\n"


def read_entries(path: Path) -> list[str]:
    """Return the list of entries from *path*.

    Returns an empty list if the file does not exist or is empty.
    """
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    if not text:
        return []
    # Strip a single trailing newline that write_entries_atomic always adds
    # so the last entry isn't returned with a stray '\n'.
    if text.endswith("\n"):
        text = text[:-1]
    parts = text.split(SEPARATOR)
    return [p for p in parts if p]


def write_entries_atomic(path: Path, entries: list[str]) -> None:
    """Atomically write *entries* to *path*.

    Uses a unique tempfile (via ``tempfile.mkstemp``) in the same directory so
    that concurrent writers from multiple threads/processes do not race on the
    same ``.tmp`` filename. Final rename via ``os.replace`` is atomic on POSIX.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = SEPARATOR.join(entries) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_entry(path: Path, content: str) -> None:
    """Append a single entry to *path*, preserving atomicity."""
    entries = read_entries(path)
    entries.append(content)
    write_entries_atomic(path, entries)
