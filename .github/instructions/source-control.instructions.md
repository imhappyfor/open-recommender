---
applyTo: "**"
---

Follow these rules for all source control operations in this repository.

## Before every commit

- Run `git diff --staged` and review every line before committing. Never use `git add -A` or `git add .` without reading the staged output first.
- Confirm tests pass before committing to `main`.
- Confirm no sensitive files are staged (see prohibited files below).

## Commit messages

Use Conventional Commits format:

```
<type>: <short summary>

<optional body explaining the why, not just the what>

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

Valid types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`.

Keep the summary line under 72 characters. Use the body to explain *why* the change was made when the reason is not obvious from the code.

## Branch rules

- Never force-push to `main` (`git push --force` or `git push -f`).
- Never amend or rebase commits that have already been pushed to a remote branch.
- Never commit with `--no-verify`.
- Prefer small, focused branches over long-lived feature branches.

## Files that must never be committed

Even if they are not in `.gitignore`, never stage or commit:

- `*.orf` — user profile documents (may contain personal preference data)
- `*.orf.key` — Ed25519 private keys (catastrophic if leaked)
- `*.orfb` — encrypted backup bundles
- `*.db`, `*.db-shm`, `*.db-wal` — SQLite databases (contain user events and grant records)
- `.env`, `.env.*` — environment files with secrets
- Any file containing a token, password, API key, or sync secret — even in a comment or test fixture
- Log files or crash dumps

If a file like this is accidentally staged, remove it with `git reset HEAD <file>` before committing. If it was already committed, treat it as a secret leak: rotate the secret immediately, then rewrite history with `git filter-repo`.

## Atomic commits

Each commit should represent one logical change. Do not bundle unrelated fixes, refactors, and features in the same commit. If a change is too large to describe in one summary line, split it.

## What requires user approval before committing

See `tooling-safety.instructions.md`. Do not commit changes to git configuration, CI secrets, or developer environment files without explicit approval.
