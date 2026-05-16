"""Offline quickstart for hermes-foundry-memory-plugin.

Runs the full provider hook chain using ``MockFoundryClient`` so you can
exercise the plugin without an Azure subscription.

Usage:
    python examples/quickstart.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hermes_foundry_memory import FoundryMemoryProvider
from hermes_foundry_memory.client import MemoryRecord, MockFoundryClient


class DemoCtx:
    """Stand-in for the Hermes plugin host context."""

    def __init__(self) -> None:
        self.providers: list = []

    def register_memory_provider(self, provider) -> None:
        self.providers.append(provider)
        print(f"[host] registered provider: {provider.name}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        hermes_home = Path(tmp)

        # 1. Build a provider directly with a MockFoundryClient (no Azure).
        mock = MockFoundryClient(
            preset_search_results=[
                MemoryRecord(content="user prefers concise replies",
                             namespace="/builtin-primary/memory/"),
                MemoryRecord(content="user lives in UTC+8",
                             namespace="/builtin-primary/memory/"),
            ],
        )
        provider = FoundryMemoryProvider(
            client=mock,
            config={"primary_mode": True, "user_id": "demo-user", "ha_backup": True},
            hermes_home=hermes_home,
        )

        # 2. Simulate the host calling register().
        ctx = DemoCtx()
        ctx.register_memory_provider(provider)

        # 3. Lifecycle: initialize → memory write → conversation turn → tool call.
        provider.initialize(
            session_id="quickstart-1",
            user_id="demo-user",
            agent_context="primary",
        )
        print("[provider] initialized")

        provider.on_memory_write("memory", "add", "user is building an Azure plugin")
        provider.on_memory_write("user", "add", "preferred_name=Avery")
        provider.sync_turn("hi there", "hello! how can I help?")

        # Wait for async worker to drain.
        provider._wait_idle(timeout=2.0)

        # 4. Invoke a tool exactly like the agent runtime would.
        raw = provider.handle_tool_call("azurememory_search", {"query": "preference"})
        print("[tool] azurememory_search →", json.dumps(json.loads(raw), indent=2))

        raw = provider.handle_tool_call("azurememory_recent", {"k": 5})
        print("[tool] azurememory_recent →", json.dumps(json.loads(raw), indent=2))

        # 5. Show what hit the (mock) Foundry API.
        print("\n[mock] recorded API calls:")
        for call in mock.calls:
            print("  -", call[0], call[1:])

        # 6. Local cache files persisted by the provider.
        print("\n[local] MEMORY.md →",
              (hermes_home / "memories" / "MEMORY.md").read_text())
        print("[local] USER.md →",
              (hermes_home / "memories" / "USER.md").read_text())

        # 7. Tidy up.
        provider.shutdown()
        print("[provider] shutdown complete")


if __name__ == "__main__":
    main()
