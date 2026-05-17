# Architecture

Open Recommender is a portable identity and state mechanism. The `.orf` file is not just a data container—it is the **lock-and-key** that eliminates account creation friction and enables continuous, portable personalization across sites.

This document describes the architecture of the Python reference implementation.

## Core Promise: Portable Identity & State

The `.orf` file serves three essential functions:

1. **Identity lock-and-key**: Your Ed25519 key pair embedded in the `.orf` file is your unforgeable identity. Sites authenticate you by verifying you can sign a challenge with your private key. No username. No password. No account creation.

2. **State container**: Your `.orf` file is the canonical source of your preference state. Sites sync with this state; they don't create their own copy. When you visit a new site, it pulls the delta (changes since last sync) and renders personalization immediately.

3. **Portability substrate**: Because your identity and state live in a portable file, you carry them everywhere. Delete your account on one site, sign in on another with the same `.orf` file, and your preferences are immediately there.

**The delta-sync contract:** Sites that support ORF must follow this pattern:
- On page load, check if the user has an `.orf` file (device presence).
- If yes, pull the delta from the user's profile (newer events since last sync timestamp).
- Apply those events locally to re-render personalization.
- Save the new sync timestamp to the `.orf` file (via the trust app or CLI).

This contract makes "create an account" obsolete. The `.orf` file is the account.

---

## High-level architecture

The project has four main runtime pieces:

1. **ORF profile model** in `models.py`
2. **Ed25519 crypto utilities** in `crypto.py`
3. **Hosted FastAPI service** in `service.py`
4. **SQLite persistence layer** in `store.py`

The CLI in `cli.py` ties those pieces together for local creation, editing, export, and sync.

## ORF profile model

`ORFProfile` is the central state object. A profile document currently contains:

- `schema_version`
- `profile_id`
- `display_name`
- `public_key`
- `created_at` and `updated_at`
- `topics`
- `opt_out_topics`
- `consent`
- `sync`
- `event_log`

### Identity model

- Key pairs are Ed25519.
- Public keys are stored as URL-safe base64 strings.
- `profile_id` is derived from the first 32 hex characters of the SHA-256 hash of the public key bytes and formatted as `orf:profile:<digest>`.

This means identity is bound to the embedded public key rather than a separate database-generated identifier.

### Topic model

Topic names must use a namespaced format like `orf:technology/python`.

Allowed topic visibility values:

- `public`
- `selective`
- `private`

Only `public` topics appear in the public projection, and even then only when:

- `consent.share_public_topics` is `true`
- the topic is not listed in `opt_out_topics`

The Phase 2 contract foundation also adds a **consented projection** helper for pilot-site access:

- `public` topics are included only when the approved scope set contains `topics.public`
- `selective` topics are included only when the approved scope set contains a matching
  `topics.selective:<topic-name>` scope
- `private` topics are never included
- opted-out topics stay hidden even if a scope would otherwise include them

### Consent model

Current stored consent fields:

- `share_public_topics`
- `ad_personalization`
- `hosted_sync`

The public projection exposes only:

- `share_public_topics`
- `ad_personalization`

### Event log and merge rules

All local profile mutations are represented as signed events.

Supported operations:

- `set_topic`
- `remove_topic`
- `set_consent`
- `set_opt_out`
- `set_profile`

Important merge behavior implemented today:

- topic updates apply when the incoming clock is greater than or equal to the current topic clock
- topic removals apply only when the incoming clock is strictly greater than the current topic clock
- same-clock topic add therefore wins over same-clock remove
- consent updates prefer the higher clock, and on equal clocks a revocation (`false`) wins over an existing `true`
- opt-out updates prefer the higher clock, and on equal clocks opting out (`true`) wins over not opting out

These rules are covered by `tests/test_models.py`.

## Phase 2 consent contract foundation

`models.py` now includes shape helpers for the first pilot-site access contracts:

- `SiteAccessRequest`
- `AccessGrant`
- `GrantSession`

These are intentionally narrow, reference-model contracts for manually registered pilot sites.
They define the stable fields used by the current hosted request, approval, exchange, and
short-lived session flow.

### Scope model

The supported v0 scope set is intentionally small:

- `profile.read`
- `topics.public`
- `topics.selective:<topic-name>`
- `consent.summary`

Unknown scopes are ignored by default when normalizing a scope set so additive pilot-site
experiments do not break consumers.

### Compatibility behavior

The repo now exposes compatibility helpers that define the current contract behavior:

- semantic versions use `major.minor.patch`
- matching major versions are treated as supported
- unknown additive fields are ignored by core behavior and preserved on the contract helpers as
  `extra_fields`
- unknown scopes are ignored by default during normalization and consented-projection generation

This keeps the v0 contract narrow while still allowing additive fields in later minor revisions.

## Signing model

The crypto layer provides:

- canonical JSON encoding
- Ed25519 key generation
- PEM serialization for private keys
- signature creation and verification

Events are signed over the unsigned event payload:

- `event_id`
- `profile_id`
- `device_id`
- `clock`
- `timestamp`
- `op`
- `payload`

The server verifies those signatures before accepting events.

## Hosted API

`create_app()` builds a FastAPI app backed by `SQLiteStore`.

### Routes

- `GET /health` — liveness check
- `POST /profiles` — register or update a profile document
- `GET /profiles/{profile_id}/public` — fetch the public projection
- `GET /profiles/{profile_id}/events` — **list stored events after a given clock** ← Sites call this on page load to pull the delta
- `POST /profiles/{profile_id}/events` — append verified signed events
- `POST /profiles/{profile_id}/challenges` — mint a challenge for a known profile
- `POST /profiles/{profile_id}/challenge-response` — verify a signature over that challenge
- `POST /profiles/{profile_id}/site-access-requests` — create a pilot-site scoped access request
- `GET /site-access-requests/{request_id}` — inspect a pending or resolved request
- `POST /site-access-requests/{request_id}/approve` — approve requested scopes
- `POST /site-access-requests/{request_id}/deny` — deny a request explicitly
- `POST /site-access-requests/{request_id}/exchange` — start the grant exchange challenge
- `POST /site-access-requests/{request_id}/verify` — verify the exchange challenge and mint a grant session
- `GET /grant-sessions/{session_id}/projection` — read the consented projection for a verified session
- `GET /demo/site/{profile_id}` — build an immediate personalization preview from public profile data
- `POST /demo/site/{profile_id}/challenge` — create a demo challenge and return personalization preview plus challenge payload
- `POST /demo/site/{profile_id}/verify` — verify proof of control and return a verified portable-profile session response
- `GET /admin/pilot-sites` — inspect the manually seeded pilot-site registry when admin access is enabled
- `GET /admin/audit-events` — inspect recent audit records when admin access is enabled

### Service configuration and guardrails

`create_app()` supports a small set of deployment knobs:

- `OPEN_RECOMMENDER_DB_PATH`
- `OPEN_RECOMMENDER_ADMIN_TOKEN`
- `OPEN_RECOMMENDER_RATE_LIMIT_WINDOW_SECONDS`
- `OPEN_RECOMMENDER_RATE_LIMIT_MAX_REQUESTS`

Operational guardrails in the current reference service:

- auth-sensitive routes use per-client fixed-window rate limiting
- admin inspection routes are disabled unless an admin token is configured
- audit records are written for request lifecycle changes, challenge issuance and verification, grant sessions, and projection reads

### Challenge flow

The challenge system is currently a proof-of-key-control flow:

1. the server creates a challenge with `challenge_id`, `profile_id`, `nonce`, and `created_at`
2. the client signs that payload with the profile private key
3. the server verifies the signature against the stored public key
4. the challenge is marked used after a successful verification

This is not a passkey or browser-auth flow; it is a direct Ed25519 signature check.

### Demo flow

The demo routes are intentionally API-first. They provide a concrete version of the product story already supported by the current backend:

1. a site can read a public profile and derive a first-pass personalized feed immediately
2. the user can prove control of the portable profile by signing a server-issued challenge
3. after verification, the server can treat the interaction as a portable-profile session without requiring a site-specific account

The current demo personalization is deterministic and simple:

- sort public topics by descending weight
- choose up to three featured topics
- derive placeholder recommendation records from those topics
- fall back to a neutral starter feed when the public profile has no shareable topics

---

## Phase 2 Contract: Delta Sync & Continuous Personalization

Open Recommender's value proposition depends on the delta-sync contract:

**For every page load on a site that supports ORF:**

1. **Site detects ORF presence** — check if the browser has the ORF file (via storage, device presence, or user QR scan).
2. **Pull delta on page load** — call `GET /profiles/{profile_id}/events?after_clock={last_sync_clock}` to retrieve only events newer than the user's last sync.
3. **Apply events locally** — replay events into an in-memory profile state. This gives the site the current preference snapshot.
4. **Render personalization** — rank feed / recommendations using the current state.
5. **Save sync timestamp** — update `last_sync_clock` in the user's `.orf` file (via trust app, CLI, or device-stored state).

**Result:** New users signing in with their `.orf` file see personalization *immediately* (no cold start). Existing users pulling delta changes see updated personalization without lag. The `.orf` file is always in sync because the site respects the sync timestamp contract.

This delta-sync loop is the key to proving that portability is not overhead—it is the competitive advantage.

## Storage model

`SQLiteStore` persists the reference-service state in SQLite tables including:

- `profiles`
- `events`
- `challenges`
- `sites`
- `access_requests`
- `grants`
- `grant_sessions`
- `audit_events`

Behavior to know:

- profile upserts reject a reused `profile_id` with a different public key
- saving a profile with an event log rebuilds and verifies state from signed events
- appended events are stored individually and also merged back into the saved profile document
- event listing is ordered by `(clock, timestamp, event_id)`

## CLI workflow

The CLI currently supports:

- `create`
- `topic-set`
- `topic-remove`
- `consent-set`
- `opt-out-set`
- `export-public`
- `sync-push`
- `sync-pull`

The CLI is intentionally thin:

- it reads and writes local JSON files
- it stores the private key next to the profile by default as `<profile>.key`
- it signs mutations locally before applying them
- it uses standard library HTTP requests for sync calls
