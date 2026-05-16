"""Configuration loading / saving for the Foundry memory provider."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "foundry_endpoint": None,
    "memory_store_name": "hermes_user_mem",
    "chat_model": "gpt-4o",
    "embedding_model": "text-embedding-3-small",
    "auth_mode": "default",
    "api_key": None,
    "primary_mode": True,
    "user_id": "default-user",
    "ha_backup": False,
}


def load_config(path: Path) -> dict[str, Any]:
    """Load a JSON config from *path*, falling back to ``DEFAULT_CONFIG``.

    A missing file or malformed JSON both fall back to a fresh copy of the
    defaults.  A warning is logged on parse errors.
    """
    p = Path(path)
    if not p.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("config root must be a JSON object")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("failed to parse config %s: %s; using defaults", p, exc)
        return copy.deepcopy(DEFAULT_CONFIG)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_env(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *cfg* with selected env vars overlaid."""
    out = dict(cfg)
    env = os.environ
    if env.get("FOUNDRY_ENDPOINT"):
        out["foundry_endpoint"] = env["FOUNDRY_ENDPOINT"]
    if env.get("FOUNDRY_MEMORY_STORE"):
        out["memory_store_name"] = env["FOUNDRY_MEMORY_STORE"]
    if env.get("FOUNDRY_USER_ID"):
        out["user_id"] = env["FOUNDRY_USER_ID"]
    return out


__all__ = ["DEFAULT_CONFIG", "load_config", "save_config", "merge_env"]
