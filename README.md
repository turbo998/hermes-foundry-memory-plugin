# hermes-foundry-memory-plugin

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-89%20passing%20%2B%201%20skipped-brightgreen.svg)](#development)
[![Hermes plugin](https://img.shields.io/badge/hermes--agent-plugin-8A2BE2.svg)](https://github.com/NousResearch)
[![Maturity](https://img.shields.io/badge/maturity-beta-orange.svg)](#)

> **Azure equivalent of [`guanquntang/hermes-agentcore-memory`](https://github.com/guanquntang/hermes-agentcore-memory).**
> Same Hermes Agent `memory_provider` contract, swapped onto Azure AI Foundry
> (Memory Stores + Threads) instead of AWS Bedrock AgentCore + S3.

A `memory_provider` plugin for [Hermes Agent](https://github.com/NousResearch)
backed by **Azure AI Foundry Memory Stores** and **Foundry Threads**. It is the
Azure-native counterpart of
[`hermes-agentcore-memory`](https://github.com/guanquntang/hermes-agentcore-memory)
(which targets AWS Bedrock AgentCore + S3).

The implementation plan lives at
[`.hermes/plans/2026-05-16_111500-foundry-memory-plugin.md`](.hermes/plans/2026-05-16_111500-foundry-memory-plugin.md).

---

## Highlights

- **Primary-mode sync** — Foundry Memory Stores act as the source of truth for
  agent-curated facts; local `MEMORY.md` / `USER.md` files are kept as a fast
  on-disk cache.
- **Two-layer architecture** — *Layer 1*: bounded, fast, local cache.
  *Layer 2*: Foundry Memory Stores (semantic search, summarization, episodic
  memory) + Foundry Threads (recent-turn history).
- **HA backup namespace** — optional mirror under `/builtin-backup/...` for
  disaster recovery.
- **Resilience** — circuit breaker, retry-with-backoff, atomic file writes,
  cross-process pull-lock.
- **Drop-in tools** — `azurememory_search`, `azurememory_list`,
  `azurememory_recent` are exposed automatically to the agent.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Hermes Agent                           │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │  Memory tool        │◀──▶│  FoundryMemoryProvider     │  │
│  │  (MEMORY.md/USER.md)│    │   • initialize() pull      │  │
│  └─────────▲───────────┘    │   • on_memory_write() push │  │
│            │ cached         │   • sync_turn() → Threads  │  │
│            │                │   • azurememory_* tools    │  │
│            ▼                └────────────┬───────────────┘  │
│  Local cache layer (atomic IO, flock)    │                  │
└──────────────────────────────────────────┼──────────────────┘
                                           │ async worker
                                           ▼
                ┌─────────────────────────────────────────┐
                │      Azure AI Foundry                   │
                │  ┌───────────────┐  ┌────────────────┐  │
                │  │ Memory Stores │  │   Threads      │  │
                │  │ (long-term,   │  │ (short-term    │  │
                │  │  semantic)    │  │  turn history) │  │
                │  └───────────────┘  └────────────────┘  │
                └─────────────────────────────────────────┘
```

Namespaces used:
- `/builtin-primary/memory/`, `/builtin-primary/user/` — primary mirrors of the
  local cache files.
- `/builtin-backup/...` — optional HA mirror (`ha_backup: true`).
- `/compressed/`, `/delegation/` — auto-captured compression + sub-agent events.

---

## Installation

```bash
pip install -e .
# or, with the Azure SDK extras for live Foundry usage:
pip install -e .[azure]   # azure-identity, azure-ai-projects (when published)
```

Hermes will discover the plugin via the `plugin.yaml` entry point and call
`hermes_foundry_memory.register(ctx)`.

---

## Configuration

Config lives at **`$HERMES_HOME/foundry_memory.json`** (default
`~/.hermes/foundry_memory.json`).

```json
{
  "foundry_endpoint": "https://<your-project>.services.ai.azure.com/api/projects/<project>",
  "memory_store_name": "hermes_user_mem",
  "chat_model": "gpt-4o",
  "embedding_model": "text-embedding-3-small",
  "auth_mode": "default",
  "api_key": null,
  "primary_mode": true,
  "user_id": "default-user",
  "ha_backup": false
}
```

Environment variable overrides (highest precedence):

| Variable                 | Maps to               |
| ------------------------ | --------------------- |
| `HERMES_HOME`            | base config dir       |
| `FOUNDRY_ENDPOINT`       | `foundry_endpoint`    |
| `FOUNDRY_MEMORY_STORE`   | `memory_store_name`   |
| `FOUNDRY_USER_ID`        | `user_id`             |

---

## Authentication

The default `auth_mode` is `default` → `azure.identity.DefaultAzureCredential`,
which chains:

1. **Managed Identity** (recommended on Azure compute — App Service,
   Container Apps, AKS, Functions, VMs).
2. Workload Identity / Federated credential.
3. Azure CLI (`az login`) for local development.
4. Environment-based service principal (`AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`).

Set `auth_mode: "api_key"` + `api_key: "<key>"` as a fallback for environments
without identity.

### Required RBAC roles

On the Foundry project (or the parent resource group):

| Role                       | Scope                                | Purpose                                  |
| -------------------------- | ------------------------------------ | ---------------------------------------- |
| **Cognitive Services User** | Foundry project / AI Services account | Invoke chat & embedding deployments      |
| **Azure AI Developer**      | Foundry project                      | Read/write Memory Stores & Threads APIs  |

For local dev, assigning these to your user via `az role assignment create`
is sufficient.

---

## Tools exposed to the agent

| Tool name              | Purpose                                              |
| ---------------------- | ---------------------------------------------------- |
| `azurememory_search`   | Semantic / hybrid search over long-term memory       |
| `azurememory_list`     | Browse recent long-term records (no query)           |
| `azurememory_recent`   | Fetch the last *k* turns from the current thread     |

Schemas are emitted automatically via `FoundryMemoryProvider.get_tool_schemas()`.

---

## AWS → Azure mapping

| Concept            | AWS (agentcore, S3/Bedrock)         | Azure (this plugin)                          |
| ------------------ | ----------------------------------- | -------------------------------------------- |
| Session manager    | `MemorySessionManager`              | Foundry Memory Stores                        |
| Scope identifier   | `actor_id`                          | `user_id` scope                              |
| Append turn        | `add_turns`                         | `messages.create` on a Thread                |
| Long-term search   | `search_long_term_memories`         | `memory_stores.search_memories`              |
| Recent turns       | `get_last_k_turns`                  | `messages.list`                              |
| Bulk index records | `batch_create_memory_records`       | AI Search `IndexDocumentsBatch`              |
| Auth               | AWS SigV4 + IAM                     | `DefaultAzureCredential` (Managed Identity)  |
| Tool prefix        | `agentcore_*`                       | `azurememory_*`                              |

---

## Development

```bash
pip install -e .[dev]
python -m pytest -q
```

Try the offline quickstart (uses `MockFoundryClient`, no Azure required):

```bash
python examples/quickstart.py
```

---

## Use with Microsoft Agent Framework (MAF)

The plugin ships an **optional** adapter that exposes the three Foundry
memory operations as Microsoft Agent Framework `FunctionTool` instances,
so they can be dropped straight into an `af.ChatAgent`.

Install the optional extra:

```bash
pip install -e .[maf]   # pulls agent-framework-core>=1.4.0
```

Wire the tools into a `ChatAgent`:

```python
import agent_framework as af
from hermes_foundry_memory.client import MockFoundryClient  # or AzureFoundryClient
from hermes_foundry_memory.maf_adapter import get_maf_tools

client = MockFoundryClient()
tools = get_maf_tools(client=client, thread_id="thread-123", user_id="alice")
# tools == [foundry_search, foundry_list, foundry_recent]  (FunctionTool x3)

agent = af.ChatAgent(
    chat_client=af.AzureOpenAIChatClient(...),
    name="hermes-foundry-agent",
    instructions="Use foundry_* tools to recall long-term user memory.",
    tools=tools,
)
```

The wrappers are thin: they simply forward to the existing
`FoundryClient` methods, so all resilience / caching behavior of the
plugin is preserved.

### Roadmap

- **Native MAF MemoryStore (`af.MemoryStore` ABC) implementation** —
  full 13-method backend so Hermes Foundry memory can be plugged into
  MAF as a first-class memory backend. Tracked in
  [#1](https://github.com/turbo998/hermes-foundry-memory-plugin/issues/1).

---

## License

MIT — see [LICENSE](LICENSE).
