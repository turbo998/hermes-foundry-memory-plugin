"""Shared pytest fixtures for hermes-foundry-memory-plugin."""
from __future__ import annotations

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Provide an isolated HERMES_HOME directory for a single test.

    Creates a fresh directory under pytest's ``tmp_path`` and exports it via
    the ``HERMES_HOME`` environment variable so plugin code that resolves
    config / state from that location stays sandboxed.
    """
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
