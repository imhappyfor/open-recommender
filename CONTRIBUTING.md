# Contributing

Thanks for helping with Open Recommender.

The best contributions are small, concrete, and aligned with the code that already exists.

## Development setup

Use Python 3.10+; CI covers 3.10 and 3.13. Browser work uses Node.js 22.12+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows, activate with `.venv\Scripts\activate`.

That installs:

- the `open_recommender` package from `src/`
- FastAPI, Uvicorn, cryptography, and RFC 8785 encoding runtime dependencies
- development-only HTTP and JSON Schema validators used by the tests

If your shell exposes console scripts from the active environment, you can use `open-recommender ...`. Otherwise use `python -m open_recommender.cli ...`.

## Useful commands

Run tests:

```bash
python -m unittest discover -s tests -v
```

For ranking changes, also run `python examples/evaluate_ranking.py` and inspect
both gains and losses. The [evaluation guide](docs/ranking-evaluation.md) defines
the metrics, fixed clock, consented inputs, and synthetic-data limitations.

For browser changes, also run:

```sh
cd sdk/orf-web-sdk
npm test
cd ../react-sample-app
npm ci
npm test
```

The browser test builds the app, runs headless Chromium, and verifies consent
and exchange signatures. Python and JavaScript share signing fixtures under
`tests/fixtures/`. Keep those fixtures stable unless deliberately changing the
wire contract. Fixture keys are public test material, never production keys.

Native gateway work additionally requires macOS, Xcode, and `swift test` from
`native/macos`. The shared native proof fixture is verified in Swift, Node, and
Python. See [native setup and validation](docs/native-gateway.md) for the Chrome
build, extension permissions, the real Chrome/Swift transport test with synthetic
approval, optional Safari signing, and the
remaining installed-client validation gate. Do not run tests against real
Keychain identities or silently enable an extension for a user.

The Python suite also checks the packaged profile/event structural schema against
model-generated documents and malformed inputs. Keep the schema, model, and
[semantic validation limits](docs/protocol.md#machine-readable-structure) aligned.

The [test workflow](.github/workflows/tests.yml) runs on pushes and pull requests
with read-only repository permissions. It installs the Python package normally
(not editable), runs both language suites, tests the browser, and builds a wheel.
Do not expose credentials or personal profile files in tests or reports.

CI also checks the Python runtime dependency resolution with `pip-audit` and the
demo lockfile with `npm audit`. To repeat locally, install
`'pip-audit>=2.10.1,<3'` in a disposable environment, run `python -m pip_audit . --strict`,
then run `npm audit` from `sdk/react-sample-app/`. These checks need network access
and consult known advisories; a clean result is not proof of application security.

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
- stale uploads and retries must not erase newer signed history
- public projections must not leak private topics
- owner actions must bind action, target, and exact parameters to a one-time proof
- revoked grants must stop further use of existing sessions
- sync behavior must remain deterministic for same-clock conflict cases
- hosted API flows must stay local-testable without external services

## Repository map

- `src/open_recommender/models.py` — ORF data model, event application rules, public projection logic
- `src/open_recommender/crypto.py` — key generation, serialization, signing, verification
- `src/open_recommender/schemas/profile.schema.json` — profile/event structural contract
- `src/open_recommender/cli.py` — local profile and sync workflows
- `src/open_recommender/service.py` — FastAPI routes
- `src/open_recommender/store.py` — SQLite-backed persistence and challenge handling
- `tests/` — current regression coverage

## Documentation expectations

Please keep docs aligned with actual behavior.

- Update `README.md` when the user-facing setup, commands, or positioning changes.
- Update `docs/getting-started.md` for onboarding changes.
- Update `docs/architecture.md` when changing data contracts, merge rules, sync, API behavior, or storage assumptions.
- Update `docs/protocol.md` and shared vectors for wire or signing changes; keep
  deployment decisions in architecture rather than repeating the protocol.
- Do not document aspirational browser, passkey, or product flows that are not implemented yet.
- Keep planning, roadmap, and future-work notes out of public docs; use private session artifacts instead.

## Tests and behavior changes

The repo includes project-local testing guidance. In practice, contributors should:

- add or update tests whenever behavior changes anywhere in the repository
- keep Python coverage under `src/open_recommender/` in `tests/`
- prefer `unittest`
- focus especially on serialization, signature verification, privacy boundaries, sync merge semantics, API challenge flow, and CLI workflows
- for changes under `sdk/`, add or update the package's own unit or browser smoke tests and keep a real `test` script in `package.json`
- run the relevant package test command for SDK/browser work (`npm test` in the affected `sdk/` package) as part of the same change
