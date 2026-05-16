# hermes-foundry-memory-plugin

A `memory_provider` plugin for [Hermes Agent](https://github.com/NousResearch) that
backs long-term and short-term agent memory with **Azure AI Foundry Memory Stores**
and **Azure AI Search**. It is the Azure-native counterpart of
`hermes-agentcore-memory` (which targets AWS Bedrock AgentCore + S3).

## Status

🚧 Scaffolding only — modules under `hermes_foundry_memory/` will land in
follow-up commits (provider / client / breaker / storage / config / tools).

## Mapping vs `hermes-agentcore-memory`

| Concept            | AWS (agentcore, S3/Bedrock)         | Azure (this plugin)                          |
| ------------------ | ----------------------------------- | -------------------------------------------- |
| Session manager    | `MemorySessionManager`              | Foundry Memory Stores                        |
| Scope identifier   | `actor_id`                          | `user_id` scope                              |
| Append turn        | `add_turns`                         | `messages.create`                            |
| Long-term search   | `search_long_term_memories`        | `memory_stores.search_memories`              |
| Recent turns       | `get_last_k_turns`                  | `messages.list`                              |
| Bulk index records | `batch_create_memory_records`       | AI Search `IndexDocumentsBatch`              |

## License

MIT — see [LICENSE](LICENSE).
