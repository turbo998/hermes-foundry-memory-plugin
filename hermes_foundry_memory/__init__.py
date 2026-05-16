"""hermes-foundry-memory-plugin — Azure AI Foundry memory_provider plugin.

Azure-native counterpart of ``hermes-agentcore-memory``: backs Hermes Agent's
long-term and short-term memory with Azure AI Foundry Memory Stores + Threads.

Plugin entry point :func:`register` is invoked by the Hermes plugin host with a
context object exposing ``register_memory_provider(provider)``.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config, merge_env, save_config
from .provider import FoundryMemoryProvider

__all__ = [
    "FoundryMemoryProvider",
    "register",
    "DEFAULT_CONFIG",
    "load_config",
    "merge_env",
    "save_config",
]


def register(ctx) -> FoundryMemoryProvider:
    """Plugin entry point — construct and register a FoundryMemoryProvider.

    Resolution order:
        1. ``$HERMES_HOME/foundry_memory.json`` if present.
        2. Defaults from :data:`DEFAULT_CONFIG`.
        3. Selected environment variables (``FOUNDRY_ENDPOINT`` …) overlay.
    """
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    cfg_path = hermes_home / "foundry_memory.json"
    cfg = merge_env(load_config(cfg_path))
    provider = FoundryMemoryProvider(config=cfg, hermes_home=hermes_home)
    ctx.register_memory_provider(provider)
    return provider
