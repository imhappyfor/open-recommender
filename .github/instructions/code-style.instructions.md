---
applyTo: "**/*.py"
---

# Code Style Guide

This project is Python 3.10+ and follows a consistent style grounded in what is already established across the codebase. These rules apply to all `.py` files.

**Enforcement note:** `ruff` (formatter + linter) is the recommended enforcement tool. Adding it to `pyproject.toml` requires explicit user approval per `tooling-safety.instructions.md`. Until then, follow these rules manually. The `[tool.ruff]` config block at the bottom of this file is ready to paste in once approved.

---

## Imports

- `from __future__ import annotations` **must** be the first line of every module. This is a correctness requirement — it enables forward references and consistent annotation evaluation.
- Imports are grouped in this order, with a blank line between each group:
  1. `__future__`
  2. Standard library (`import X` before `from X import Y` within the group)
  3. Third-party packages
  4. Local (`from .module import ...`)
- Do not use wildcard imports (`from x import *`).

```python
# ✅ correct
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from .models import ORFProfile

# ❌ wrong — missing __future__, wrong group order
from .models import ORFProfile
import json
from fastapi import FastAPI
```

---

## Naming

| Kind | Convention | Example |
|------|------------|---------|
| Functions and variables | `snake_case` | `validate_topic_name`, `sync_token` |
| Classes | `PascalCase` | `ORFProfile`, `SQLiteStore` |
| Module-level constants | `UPPER_SNAKE_CASE` | `CHALLENGE_TTL_SECONDS` |
| Private helpers | leading `_` | `_http_send`, `_sync_request` |
| Type aliases | `PascalCase` | `JsonSender` |

---

## Type Annotations

- All public functions and methods **must** have full type annotations on parameters and return types.
- Use `X | Y` union syntax (not `Union[X, Y]`).
- Use `X | None` (not `Optional[X]`).
- Use `Any` sparingly. When used, add an inline comment explaining why the type cannot be narrowed.
- Use `collections.abc` types for abstract collections in signatures (`Iterable`, `Mapping`, `Callable`).

```python
# ✅ correct
def push_events(self, profile_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    ...

# ❌ wrong — missing return type, old Union syntax
def push_events(self, profile_id, events: List[dict]) -> None:
    ...
```

---

## Dataclasses and Enums

- Use `@dataclass(frozen=True)` for immutable value objects (configs, errors, DTOs).
- Use `@dataclass` (mutable) only when the object genuinely needs mutation.
- Enums should inherit from both `str` and `Enum` when the values are serialized to JSON or stored as strings:

```python
class Visibility(str, Enum):
    PUBLIC = "public"
    SELECTIVE = "selective"
    PRIVATE = "private"
```

---

## Function Signatures

- Use keyword-only arguments (`*` separator) for parameters that are configuration, flags, or safety-critical. This prevents accidental positional misuse.

```python
# ✅ correct — extra_headers is keyword-only
def _http_send(method: str, url: str, body: dict | None = None, *, extra_headers: dict | None = None) -> dict:
    ...

# ❌ wrong — extra_headers can be passed positionally
def _http_send(method: str, url: str, body: dict | None, extra_headers: dict | None) -> dict:
    ...
```

---

## Strings

- Use **double quotes** for all string literals.
- Use f-strings for interpolation. Do not use `%`-formatting or `.format()`.

```python
# ✅ correct
message = f"ORF API call failed for {method} {url}"

# ❌ wrong
message = "ORF API call failed for %s %s" % (method, url)
```

---

## Error Handling

- Raise specific exception types. Avoid bare `raise Exception(...)`.
- Always chain exceptions with `from` to preserve cause context:

```python
# ✅ correct
raise PartnerSDKError(...) from http_error

# ❌ wrong — loses original traceback
raise PartnerSDKError(...)
```

- Avoid bare `except Exception` unless you are re-raising or logging and re-raising. Always narrow the exception type when possible.

---

## Comments and Docstrings

- Code should be self-documenting through clear naming and structure. Avoid comments that just restate the code.
- Add a comment when the *why* is not obvious — especially for security boundaries, protocol constraints, or intentional workarounds.
- Public API surfaces (functions and classes in `partner_sdk.py`, `service.py`, `models.py`, `cli.py`) **should** have a one-line docstring explaining purpose. Inline implementation helpers do not require docstrings.

```python
# ✅ useful comment — explains an intentional design choice
# _sync_request bypasses injected send_json intentionally; auth is tested
# at the service level, not via SDK mocks.
def _sync_request(self, method: str, url: str, body: dict | None = None) -> dict:
    ...
```

---

## Line Length

- Target **100 characters** maximum. Ruff will enforce this when configured.
- Break long argument lists vertically with one argument per line, trailing comma on the last item:

```python
result = some_function(
    first_argument,
    second_argument,
    third_argument,
)
```

---

## Module Structure Order

Within a module, prefer this top-to-bottom order:

1. `from __future__ import annotations`
2. Standard library imports
3. Third-party imports
4. Local imports
5. Module-level constants (`UPPER_SNAKE_CASE`)
6. Type aliases
7. Public classes (enums, dataclasses, domain models first)
8. Private helpers (`_prefixed`)
9. Public functions / factory functions

---

## Ready-to-Use Ruff Config

When the user approves adding `ruff` to the project (see `tooling-safety.instructions.md`), paste this into `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
ignore = ["E501"]  # line length enforced by formatter, not linter

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

And add `ruff` to `[project.optional-dependencies]` dev extras:
```toml
"ruff>=0.4,<1",
```
