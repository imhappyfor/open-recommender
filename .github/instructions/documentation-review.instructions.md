---
applyTo: "**"
---

Keep documentation review part of normal completion, not a separate follow-up.

- After any meaningful code, API, CLI, protocol, test, workflow, or developer-experience change, review whether project docs also need updates before you finish.
- Use the `pro-documenter` skill to help audit documentation impact and catch gaps whenever the change is more than trivial or touches user-facing or contributor-facing behavior.
- Check the docs that are most likely to drift:
  - `README.md`
  - `docs/`
  - contributor or setup guidance
  - protocol, API, CLI, and operations docs
- Prefer updating existing documentation over creating duplicate docs.
- If no documentation change is needed, make that a conscious decision after review, not an assumption.
- Documentation review should happen after the implementation is materially in place, while the diff is fresh, so new behavior, commands, flags, file paths, and workflow expectations are not missed.

For this repository, be especially careful after changes to:

- ORF/profile model behavior
- privacy or consent semantics
- hosted sync or challenge-response flows
- CLI commands or local file layout
- tests that establish intended behavior
- CI, local setup, or contributor workflows
