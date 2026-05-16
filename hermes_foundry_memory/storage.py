"""Local file storage for memory entries.

Entries are joined with the separator '\n§\n' and each file ends with a
trailing newline. Writes are atomic (write-to-tmp + os.replace) and guarded
by an advisory fcntl lock on the tmp file.
"""

from __future__ import annotations

import fcntl
import os
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

    Steps:
      1. ensure parent dir exists
      2. write to ``path + .tmp``
      3. acquire exclusive fcntl lock on the tmp fd while writing
      4. ``os.replace`` tmp -> path (atomic on POSIX)
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")

    payload = SEPARATOR.join(entries) + "\n"

    # open with O_CREAT|O_WRONLY|O_TRUNC; lock; write; fsync; close; replace.
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as f:
                f.write(payload)
                f.flush()
                os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    os.replace(tmp, p)


def append_entry(path: Path, content: str) -> None:
    """Append a single entry to *path*, preserving atomicity."""
    entries = read_entries(path)
    entries.append(content)
    write_entries_atomic(path, entries)
