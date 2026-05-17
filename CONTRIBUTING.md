# Contributing

Thanks for helping with Open Recommender.

This repository is still early, so the best contributions are small, concrete, and aligned with the code that already exists.

## Development setup

Use Python 3.10+.

```bash
python -m pip install -e .[dev]
```

That installs:

- the `open_recommender` package from `src/`
- FastAPI, Uvicorn, and cryptography runtime dependencies
- the test dependency used by the hosted API tests

If your shell exposes console scripts from the active environment, you can use `open-recommender ...`. Otherwise use `python -m open_recommender.cli ...`.

## Useful commands

Run tests:

```bash
python -m unittest discover -s tests -v
```

The repository's project-local testing instructions currently reference this equivalent repo-local venv command:

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

Run the API locally:

```bash
python -m uvicorn open_recommender.service:create_app --factory --reload
```

Create and edit a local profile:

```bash
python -m open_recommender.cli create profile.orf --display-name "Alice Example"
python -m open_recommender.cli topic-set profile.orf orf:technology/python 0.9
python -m open_recommender.cli consent-set profile.orf hosted_sync false
python -m open_recommender.cli export-public profile.orf
```

## What to preserve

When changing code, keep these current project guarantees intact:

- ORF documents must remain portable JSON documents
- profile IDs must continue to derive from the embedded Ed25519 public key
- signed events must verify against the profile public key
- public projections must not leak private topics
- sync behavior must remain deterministic for same-clock conflict cases
- hosted API flows must stay local-testable without external services

## Repository map

- `src/open_recommender/models.py` — ORF data model, event application rules, public projection logic
- `src/open_recommender/crypto.py` — key generation, serialization, signing, verification
- `src/open_recommender/cli.py` — local profile and sync workflows
- `src/open_recommender/service.py` — FastAPI routes
- `src/open_recommender/store.py` — SQLite-backed persistence and challenge handling
- `tests/` — current regression coverage

## Documentation expectations

Please keep docs aligned with actual behavior.

- Update `README.md` when the user-facing setup, commands, or positioning changes.
- Update `docs/getting-started.md` for onboarding changes.
- Update `docs/architecture.md` when changing data contracts, merge rules, sync, API behavior, or storage assumptions.
- Do not document aspirational browser, passkey, or product flows that are not implemented yet.

## Tests and behavior changes

The repo includes project-local instructions in `.github/instructions/testing.instructions.md`. In practice, contributors should:

- add or update tests whenever behavior changes under `src/open_recommender/`
- prefer `unittest`
- focus especially on serialization, signature verification, privacy boundaries, sync merge semantics, API challenge flow, and CLI workflows

## Project-local Copilot setup

This repository also contains project-local Copilot configuration:

- `.github/agents/avery.agent.yaml`
- `.github/skills/`
- `.agents/skills/`
- `.github/instructions/`

`.github/skills/` is the authored source for the repo's specialist skills. `.agents/skills/` mirrors those definitions so Copilot CLI skill discovery can pick them up reliably.

These files are contributor tooling, not runtime product code. If you change developer workflows or contributor guidance in a meaningful way, update them when needed so the local agent and skill setup stays accurate.
