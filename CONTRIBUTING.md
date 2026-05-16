# Contributing

Thanks for your interest in `hermes-foundry-memory-plugin`! Pull requests are
very welcome.

## Ground rules

1. Open an issue first for non-trivial changes (new tools, breaking config,
   architecture shifts) so we can align on the approach.
2. Keep PRs focused — one logical change per PR.
3. Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
   messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:` ...).
4. **All tests must pass** before a PR can be merged:

   ```bash
   pip install -e .[dev]
   pytest -q
   ```

5. Add tests for any new behavior. Prefer the existing `MockFoundryClient`
   pattern so the suite stays offline-friendly.
6. Do not commit secrets, real Foundry endpoints, or `~/.hermes/` config dumps.

## Code style

- Python 3.11+.
- Type hints on all public functions.
- Keep modules small and dependency-light.

## Releasing

Maintainer-only. Bump version in `pyproject.toml`, update `CHANGELOG.md`, tag
`vX.Y.Z`, and publish a GitHub release.
