# Architecture

Open Recommender separates portable user state, user authorization, and site
ranking. The Python/SQLite service is a reference implementation for controlled
pilots, not a web-scale production service.

The [protocol](protocol.md) defines wire behavior. This document explains where
that behavior lives and which components must be trusted.

The separate [native gateway](native-gateway.md) is the first local-first client
slice: a SwiftUI app, Chrome extension/Swift stdio host, app-private Keychain site identities, and
backend-verifiable login proofs. No hosted profile service is involved in that
path. The Safari target reuses the same bridge and validation. It does not yet
import profiles or rank/share preferences; installed UI/Keychain behavior and
release distribution remain validation gates. The hosted paths below are unchanged.

## Components and ownership

| Component | Owns | Does not own |
| --- | --- | --- |
| User's profile and signing client | Preference history, identity key, approval decisions | A site's content catalog |
| Hosted ORF service | Verified history, scoped grants, sessions, ranking feedback | The user's private signing key |
| Partner site | Candidates, site scores, fallback feed, presentation | Permission to read raw private history |
| Reference reranker | Ordering within the approved grant boundary | Candidate generation or recommendation quality guarantees |

The CLI ties local files to the service. The React app combines a signing client
and a mock partner surface **only for localhost demonstrations**. A real partner
must keep signing in a separate, user-trusted client, not its own page or backend.

## Main paths

```text
User file + key ── signed history ──> trusted sync service
User signer    ── signed consent ──> request → grant → short-lived session
Partner        ── session + candidates ──> scoped projection or reranker
User signer    ── signed revoke/delete ──> stop access / remove live hosted rows
```

A site can read public data without approval. Reading a consented projection or
ranking through a grant requires a verified session. Raw event sync is a different
trust boundary and is not a partner-personalization API.

## Source map

- `models.py`: profile/event models, scope normalization, projections, merge rules.
- `crypto.py`: Ed25519 keys, identity fingerprints, exact signed JSON encoding.
- `store.py`: SQLite migrations, history replay, grants, challenges, sessions, audit.
- `service.py`: HTTP routes, localhost UI, shared-token gates, in-process rate limits.
- `cli.py`: local edits, sync, backup/recovery, signed owner actions.
- `recommender/feed.py`: local cross-site aggregation from recommendation events.
- `recommender/ranking.py`: replaceable baseline reranker and grant-local feedback.
- `partner_sdk.py` and `sdk/orf-web-sdk/`: thin HTTP clients.
- `examples/`: narrated pilot and sample adopter site.
- `native/macos/`: native approval app, Chrome stdio host, Safari target, bounded public-proof mailbox.
- `native/chrome/`: Chrome manifest and standalone Mac app packaging metadata; browser scripts are shared with Safari.
- `native_gateway.py` and `examples/native_site.py`: site-login verifier and loopback demo.

## Verified state, not trusted snapshots

Hosted writes acquire a SQLite write transaction before reading current state.
They verify incoming events, merge stored history, rebuild the profile from its
signed registration, and commit history plus materialized state atomically.

New events carry a signed JCS encoding marker for Python/browser interoperability.
Legacy events retain their original encoding; verification and duplicate checks
use that event's selected encoding without fallback or re-signing. New 0.2.0
profiles require JCS throughout. See [signature compatibility](protocol.md#signatures).

Unsigned topic/consent/name snapshot fields cannot override signed state.
Conflicting uses of an event ID fail; unchanged retries are idempotent. A stale
upload cannot roll the hosted profile back. Every write replays history; there
are no checkpoints or compaction promises.

The event order and tie rules are in [Protocol: events and merge](protocol.md#events-and-merge).
The API's clock filter is not a complete replication cursor. CLI pulls fetch all
post-registration history and use the same verified replay to merge local and
remote events, retaining the local device label and unsent changes.
See [sync boundaries](protocol.md#sync-boundaries) for costs and limits.

## Owner actions

The existing challenge mechanism also authorizes approval, denial, revocation,
hosted deletion, and raw sync reads. It binds a one-time signature to the owner, action, target,
and exact parameters. Verification, consumption, audit, and mutation share one
write transaction; failed actions roll back all four.

See [Protocol: owner actions](protocol.md#owner-actions) for endpoints, parameter
allowlists, encoding, and error codes. Browser mutation routes additionally
require localhost and a review token.

Revoked or expired grants are checked when loading a session, so old sessions
cannot keep reading projections, ranking, or writing feedback.
An operation already in flight is not recalled, and previously delivered copies
remain outside service enforcement.

Deletion removes rows from `profiles`, `events`, `challenges`,
`access_requests`, `grants`, `grant_sessions`, `ranking_feedback_events`, and
`audit_events` in one transaction. The service retains site registrations and its
own configuration. It does not keep a profile deletion tombstone; retained signed
history can register the same identity again.

## Storage and operation

SQLite uses WAL mode and numbered schema migrations (current version: 4).
Migration rejects a database newer than the service supports. Keep the database
and WAL on durable local storage; do not put a live database on shared network
storage or casually copy only its main file for backup.

Configuration is read by `create_app()`:

| Variable | Purpose |
| --- | --- |
| `OPEN_RECOMMENDER_DB_PATH` | Database path; default `open_recommender.db` |
| `OPEN_RECOMMENDER_PILOT_SITES_PATH` | JSON registry of permitted pilot sites |
| `OPEN_RECOMMENDER_ADMIN_TOKEN` | Enables read-only site/audit inspection |
| `OPEN_RECOMMENDER_SYNC_TOKEN` | Optional extra Bearer gate on owner reads and signed event writes |
| `OPEN_RECOMMENDER_SITE_TOKEN_HASHES` | Optional JSON object mapping registered site IDs to distinct SHA-256 token hashes |
| `OPEN_RECOMMENDER_RATE_LIMIT_WINDOW_SECONDS` | Protected-route window; default 60 |
| `OPEN_RECOMMENDER_RATE_LIMIT_MAX_REQUESTS` | Per-client/bucket limit; default 20 |
| `OPEN_RECOMMENDER_MAX_REQUEST_BYTES` | JSON request body budget; default 2 MiB |
| `OPEN_RECOMMENDER_MAX_PROFILE_EVENTS` | Profile history/event-batch count budget, including registration; default 10,000 |
| `OPEN_RECOMMENDER_MAX_RANKING_CANDIDATES` | Candidates per ranking request; default 500 |
| `OPEN_RECOMMENDER_MAX_FEEDBACK_BATCH` | Feedback events per request; default 500 |
| `OPEN_RECOMMENDER_MAX_GRANT_FEEDBACK_EVENTS` | Retained feedback events per grant; default 10,000 |
| `OPEN_RECOMMENDER_MAX_HISTORY_BYTES` | Stored event JSON budget, independently per profile and per grant feedback history; default 8 MiB |

Limits must be positive integers. `create_app()` accepts matching lowercase
keyword arguments; explicit arguments override the environment.
The request budget counts received bytes before JSON parsing, including streams
without a `Content-Length` header. Item limits run before event/candidate parsing.
Stored-history checks and writes share a SQLite write transaction, so concurrent
writes cannot both claim the final slot. Identical event retries do not grow history.
Stored byte budgets count UTF-8 event JSON, not SQLite indexes, snapshots, or audits.
The service rejects excess work with 413; it never clips or compacts signed history.
See [protocol limits](protocol.md#service-budgets) for client handling.

The default site is `open-news-demo`. The registry constrains names and allowed
scopes. Optional [partner authentication](protocol.md#partner-authentication)
binds backend credentials to the site's requests and sessions; it does not verify
domain ownership. Unset configuration leaves partner endpoints unauthenticated.
Hashes stay in process configuration, not the site registry or database. Rotate
or remove them by replacing configuration and restarting every worker. An empty
object denies all partner calls, including existing sessions. There is no live
rotation/overlap API; removal does not recall in-flight responses or copies.
`GET /health` reports liveness, schema/configuration status, token gates, and
configured budgets under `service.limits`. It is not a latency, measured capacity,
recovery, or external-dependency probe.
Live route documentation is generated at `/docs` and `/openapi.json`.
Health distinguishes `sync_token_required` (the optional shared gate) from
`sync_read_owner_proof_required` (always true). `sync_auth_required` is retained
as a legacy alias for the token flag, not a complete statement of read authorization.

## Deployment boundary

Bind the reference service to localhost for demos. In particular:

- Raw sync reads always require a one-time profile-owner proof; a shared token
  alone cannot read history. The trusted service still stores that history as plaintext.
- Responses disable caching by default; only public-profile GETs are exempt.
  An HTTP cache policy cannot recall a partner's own retained copy.
- Localhost browser checks are not a safe internet-facing auth policy behind a
  proxy that makes every request appear local.
- CORS currently admits localhost origins only; changing it is not site authentication.
- Sessions are bearer credentials, not origin-bound. Configured partner auth
  additionally binds their use to a backend credential. Keep both out of browser
  bundles, analytics, access logs, and unrelated third-party requests.
- Rate-limit state is per process and in memory. Counters are thread-safe, expired
  entries are pruned once per window, and at most 10,000 client/bucket pairs are
  retained. A full limiter rejects new pairs with 429 until slots are reclaimed;
  existing pairs keep their own limits. Multiple workers do not share this state.
- Profile uploads, event appends, and Lens imports share the `profile-write` rate
  bucket. A 429 response includes `Retry-After`; limits are not partner authentication.
- Per-request and per-history budgets are not global disk quotas or connection
  limits. Profiles, grants, challenges, and audits can still accumulate. Operators
  need disk monitoring, retention, connection/body-read timeouts, and upstream
  concurrency controls before exposing the service to untrusted traffic.
- SQLite serializes writes; full replay per write grows with history size.
- The baseline ranker has tests, not a production relevance benchmark or SLA.

These are deployment constraints, not a claim of readiness for large public sites.
An internet-facing deployment needs a separate reviewed auth, storage, retention,
and operations boundary. Current privacy and recovery limits are documented in
[Transparency & Security](transparency-and-security.md).
