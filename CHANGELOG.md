## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-16

### Added
- Initial public release of `hermes-foundry-memory-plugin` — Azure-native
  `memory_provider` for Hermes Agent backed by **Azure AI Foundry Memory
  Stores** and **Foundry Threads**.
- Primary-mode sync between Hermes local cache (`MEMORY.md` / `USER.md`) and
  Foundry Memory Stores; local files act as a fast read cache.
- Two-layer memory architecture (bounded local cache + Foundry semantic store).
- Optional HA backup namespace under `/builtin-backup/...`.
- Resilience primitives: circuit breaker, retry-with-backoff, atomic file
  writes, and a cross-process pull-lock.
- Agent-facing tools auto-registered on load:
  `azurememory_search`, `azurememory_list`, `azurememory_recent`.
- `FoundryClient` abstraction with `MockFoundryClient` (offline) and Azure SDK
  implementation stub.
- `register(ctx)` entry point + Hermes integration test.
- Quickstart example (`examples/quickstart.py`) runnable without Azure.
- 89 passing tests (1 skipped — live Azure-only path).

[0.1.0]: https://github.com/turbo998/hermes-foundry-memory-plugin/releases/tag/v0.1.0
