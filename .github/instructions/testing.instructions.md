---
applyTo: "**/*.py"
---

Keep testing aligned with behavior changes as this project grows.

- For changes under `src/open_recommender/`, add or update tests in `tests/` in the same change whenever behavior, data contracts, merge rules, auth flows, or API responses change.
- Prefer `unittest` unless the project explicitly adopts a different framework later.
- Treat these areas as high-risk and make them hard to regress:
  - ORF serialization and deserialization
  - signature verification and signed event handling
  - public projection privacy boundaries
  - sync merge/conflict semantics
  - hosted API challenge and event-ingest flows
  - CLI create/edit/sync workflows
- Use temporary files and temporary SQLite databases in tests. Do not depend on network access or external services.
- Keep tests deterministic: control clocks, timestamps, ordering assumptions, and event IDs whenever assertions depend on ordering or merge outcomes.
- When adding new fields to the ORF document or event payloads, include backward-compatible parsing coverage and update the public projection tests if visibility or consent behavior changes.
- For hosted API work, favor request/response tests through FastAPI's test client over brittle implementation-detail assertions.
- For CLI work, prefer subprocess or command-level smoke tests that exercise real file reads and writes.
- If Python code changed during the session, do not conclude until you have run the relevant `unittest` coverage for that work.
- When the change touches multiple areas, affects shared behavior, or you are unsure what is sufficient, run the repository test command before concluding:

  `./.venv/bin/python -m unittest discover -s tests -v`
