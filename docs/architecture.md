# Architecture

Open Recommender is a portable identity and preference state mechanism. The `.orf` file carries a user's identity and topic preferences so they can bring them to any site that supports the format.

This document describes the architecture of the Python reference implementation.

## Core design: portable identity and state

The `.orf` file serves three functions:

1. **Identity**: The Ed25519 key pair embedded in the `.orf` file is the user's identity. Sites authenticate users by verifying they can sign a challenge with their private key.

2. **State container**: The `.orf` file holds the user's preference state as a signed event log. The events endpoint lets sites pull recent changes since a given clock value.

3. **Portability**: Because identity and state live in a portable file, users can bring their preferences to any site that uses the events-based delta-sync contract.

---

## High-level architecture

The project has five main runtime pieces:

1. **ORF profile model** in `models.py`
2. **Recommendation aggregation** in `recommender/feed.py`
3. **Ed25519 crypto utilities** in `crypto.py`
4. **Hosted FastAPI service** in `service.py`
5. **SQLite persistence layer** in `store.py`

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

The consent model also defines a **consented projection** for pilot-site access:

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

Important merge behavior implemented in the current reference implementation:

- topic updates apply when the incoming clock is greater than or equal to the current topic clock
- topic removals apply only when the incoming clock is strictly greater than the current topic clock
- same-clock topic add therefore wins over same-clock remove
- consent updates prefer the higher clock, and on equal clocks a revocation (`false`) wins over an existing `true`
- opt-out updates prefer the higher clock, and on equal clocks opting out (`true`) wins over not opting out

These rules are covered by `tests/test_models.py`.

## Consent contract models

`models.py` includes shape helpers for pilot-site access contracts:

- `SiteAccessRequest`
- `AccessGrant`
- `GrantSession`

These are narrow, reference-model contracts for manually registered pilot sites.
They define the stable fields used by the hosted request, approval, exchange, and
short-lived session flow.

### Scope model

The supported scope set is intentionally small:

- `profile.read`
- `topics.public`
- `topics.selective:<topic-name>`
- `consent.summary`

Unknown scopes are ignored by default when normalizing a scope set so additive pilot-site
experiments do not break consumers.

### Compatibility behavior

The repo includes compatibility helpers that define the contract behavior:

- semantic versions use `major.minor.patch`
- matching major versions are treated as supported
- unknown additive fields are ignored by core behavior and preserved on the contract helpers as
  `extra_fields`
- unknown scopes are ignored by default during normalization and consented-projection generation

This keeps the contract narrow while still allowing additive fields in later minor revisions.

## Local recommender feed

Cross-site recommendation aggregation now lives in `src/open_recommender/recommender/feed.py`.
The reference implementation keeps legacy imports available from `open_recommender.models`
so existing callers can continue importing `AggregatedFeed`,
`AggregatedRecommendation`, and `RecommendationItem` without changes.

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
- `POST /grant-sessions/{session_id}/rank` — rerank site-generated candidates within the verified grant-session boundary
- `POST /grant-sessions/{session_id}/rank/feedback` — ingest explicit site-local ranking outcomes scoped to that grant
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
- ranking feedback is stored in a service-side table keyed by grant/session and is never written into the ORF document
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

## Delta-sync events endpoint

The hosted service exposes an events endpoint that sites can use to pull a user's preference changes since a given clock value:

```
GET /profiles/{profile_id}/events?after_clock={last_sync_clock}
```

This returns only events newer than the provided clock. A site can store the returned `last_clock` value and use it on the next pull to retrieve only new changes.

Event types returned:

- `set_topic` — user updated a topic weight or visibility
- `remove_topic` — user removed a topic
- `set_consent` — user changed a consent flag
- `set_opt_out` — user opted a topic in or out of sharing
- `recommend` — a recommendation pushed from a site to the profile event log

Sites calling this endpoint need the user's `profile_id` and (if sync token gating is enabled) a sync token.

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

The CLI supports:

- `create`
- `topic-set`
- `topic-remove`
- `consent-set`
- `opt-out-set`
- `export-public`
- `sync-push`
- `sync-pull`
- `site-access-request-get`
- `site-access-request-approve`
- `site-access-request-deny`
- `grant-session-projection`
- `backup-create`
- `backup-restore`
- `feed show`

The CLI is intentionally thin:

- it reads and writes local JSON files
- it stores the private key next to the profile by default as `<profile>.key`
- it signs mutations locally before applying them
- it uses standard library HTTP requests for sync calls
