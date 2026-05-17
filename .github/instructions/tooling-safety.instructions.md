---
applyTo: "**"
---

Never change developer tooling preferences or system configuration without explicit approval from the user.

This includes, but is not limited to:

- `git config` (user.name, user.email, or any other git setting — local or global)
- Shell configuration files (`.zshrc`, `.bashrc`, `.bash_profile`, `.profile`, etc.)
- SSH or GPG key configuration
- Package manager global settings (`pip`, `npm`, `brew`, etc.)
- IDE or editor settings
- Environment variables written to persistent files
- `pyproject.toml` tool configuration beyond what is directly required by the task
- CI/CD secrets, tokens, or credentials

If a task cannot be completed without modifying one of these, stop and ask the user for explicit permission before proceeding. Describe exactly what would change and why it is needed.

Do not assume a configuration is missing or wrong just because a command fails. Check existing global or system-level config before attempting to set anything.
