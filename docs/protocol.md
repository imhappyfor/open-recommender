# ORF wire contract

This is the implemented **developer-preview contract**, not a ratified standard
or an internet-scale security specification. It covers profile schemas **0.1.0 and 0.2.0**
and access/ranking contract **0.3.0**. Package versions are independent.

The rules below describe the reference implementation. An independent client
must pass the shared signing vectors and preserve the stated privacy boundaries;
passing the vectors alone does not prove full event or service conformance.

## Identity and files

- `.orf` is UTF-8 JSON containing `schema_version`, `profile_id`, `public_key`,
  `display_name`, `created_at`, `updated_at`, `topics`, `opt_out_topics`, `consent`,
  `sync`, and `event_log`.
- Keys are Ed25519. `public_key` is the base64url encoding of the 32 raw public-key
  bytes, not a PEM or DER wrapper. Readers accept omitted base64 padding.
- `profile_id` is `orf:profile:` followed by the first 32 lowercase hex characters
  of SHA-256 of those raw bytes. An ID that does not match the key is rejected.
- The private key is a separate PKCS#8 PEM file, conventionally `<profile>.key`.
  A site must not request this key. The localhost demo is not a production wallet.
- `.orfb` backups encrypt only the recovery key, not the JSON profile/history.
- Snapshot fields are readable conveniences. Hosted state is rebuilt from
  verified history; editing a snapshot is not a signed mutation.

### Machine-readable structure

[Profile schema](../src/open_recommender/schemas/profile.schema.json) uses JSON
Schema Draft 2020-12 and is included in the Python package. The same file's
`#signedEvent` anchor validates an event envelope and its operation payload.
All references are local; validation needs no hosted schema registry.

With the development dependencies installed:

```python
import json
from importlib.resources import files
from jsonschema import Draft202012Validator

schema = json.loads(files("open_recommender").joinpath("schemas/profile.schema.json").read_text())
validator = Draft202012Validator(schema)
validator.validate(document)  # Parsed profile structure only
validator.evolve(schema={"$ref": "#signedEvent"}).validate(event)
```

This is **not a service-admission or security check**. Separately reject
non-finite/non-JSON values, unsupported versions, blank event identifiers, invalid
topics/timestamps, mismatched key fingerprints, invalid signatures, conflicting
event IDs, and invalid registration/history. JSON Schema treats `1.0` as an
integer; the reference parser requires integer tokens for clocks. Timestamps
deliberately have no RFC 3339 `format` assertion: the reference accepts a broader
timezone-aware ISO 8601 grammar. Snapshot topic timestamps are emitted as strings;
the legacy parser can also coerce them to strings.

The six identity/metadata strings are required; omitted snapshot collections,
consent, sync, and history retain the reference parser's defaults. An empty
history passes the structural schema but cannot register with the service.
Unknown fields are allowed: outer profile/event fields may be discarded;
unknown payload fields remain signed. Do not mutate or fill defaults in signed
events during validation. Schema acceptance grants no site access and does not
make a full private profile safe to share with partners.

## Signatures

Ed25519 signs **exact canonical JSON bytes**, without a prehash. Signatures are
base64url; padding is accepted. An event's encoding is selected explicitly:

| `signature_encoding` | Encoding |
| --- | --- |
| `"jcs-rfc8785"` | RFC 8785/JCS; the marker itself is signed |
| Absent | Legacy Python encoding below; also used for challenges |

Unknown markers and explicit `null` are rejected. Never retry verification with
a different encoding. Sign exactly the envelope fields listed under
[Events and merge](#events-and-merge), including the marker when present.
The outer `signature` and unrecognized outer fields are not in that envelope;
unknown fields inside `payload` remain signed.

### JCS event encoding

New profiles use schema **0.2.0** and require the JCS marker on every event,
including registration. Existing **0.1.0** histories retain their original
signatures; the current CLI appends new JCS events without rewriting old ones.
Editing an unsigned snapshot's version does not migrate its signed registration.
Older clients must upgrade before reading or writing marked events.

[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) defines recursive UTF-16 key
ordering, ECMAScript number serialization, and UTF-8 output without whitespace.
Array order is retained and strings are not Unicode-normalized. `1.0` and `1`
produce the same bytes; negative zero becomes `0`. Numbers must be finite binary64
values and strings/keys must not contain lone surrogates. The Python parser accepts
large integer tokens only when their value is exactly representable as binary64;
use strings for exact large identifiers. Event clocks remain safe integers.
File imports and JSON request bodies reject duplicate object fields before
interpreting signatures; do the same in independent clients.

Python uses `canonical_signed_json` with the `rfc8785` library; the browser SDK
exports `canonicalEventJsonBytes`. These encoders do not grant permission or
replace semantic event, identity, registration, and history validation.
Schema 0.2.0's mandatory marker is a semantic rule, not enforced by the shared
structural schema alone.

### Legacy encoding

Unmarked events and service challenges preserve this Python encoding:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"),
           ensure_ascii=True, allow_nan=False).encode("utf-8")
```

Object keys are sorted recursively, arrays retain order, there is no whitespace,
and non-ASCII characters are escaped with lowercase `\uXXXX` sequences (surrogate
pairs for characters outside the basic multilingual plane). Strings are not
Unicode-normalized. Omission and explicit `null` are different signed values.
This encoding is **not RFC 8785/JCS**.
Non-finite numbers (`NaN`, positive/negative infinity) are rejected, including
inside nested metadata. Encoding of valid finite values is unchanged.

### Browser signing boundary

Service-generated challenges contain ASCII field names and string values.
`canonicalJsonBytes` additionally handles booleans, null, arrays, plain objects,
and safe integers; it rejects floats, negative zero, non-ASCII keys, and unsupported
values. Python and JavaScript can serialize floats differently (`1.0` versus `1`).
Do not use that helper for event creation: use `canonicalEventJsonBytes` for marked
JCS events in a user-trusted signer, never in normal partner code. Browser file
import must still forward the original JSON text: parse/stringify can invalidate
legacy events, including mixed 0.1.0 histories. `upsertProfile` accepts that text
directly; the reference browser demos use this path.

[Shared vectors](../tests/fixtures/signing-vectors.json) contain the public key,
derived identity, input payload, expected canonical UTF-8 hex, and Ed25519 signature.
Both Python and JavaScript tests verify them, including Unicode escaping, nested
key ordering, and an owner-action hash. The fixture key is public test material;
never use it for an actual profile.

[Event vectors](../tests/fixtures/event-signing-vectors.json) cover every operation,
fractional/integral weights, Unicode values and UTF-16 key ordering, numeric keys,
large and subnormal numbers, expected JCS bytes, and Ed25519 signatures. Python
and JavaScript both sign and verify these vectors, including JavaScript number
round trips. Their deterministic key is also public test material only.

## Events and merge

Each event contains these signed fields, plus an unsigned `signature` field:

| Field | Meaning |
| --- | --- |
| `event_id` | Unique string deduplication key; CLI generates a UUID |
| `profile_id` | Identity whose key signs the event |
| `device_id` | Origin label, not independent device authorization |
| `clock` | Non-negative logical safe integer, at most `2^53 - 1`; never a boolean or string |
| `timestamp` | ISO 8601 string with a timezone; generated UTC with whole seconds |
| `op` | Known operation name |
| `payload` | Operation-specific JSON object |
| `signature_encoding` | `"jcs-rfc8785"` for new events; omitted only on legacy events |

Hosted registration requires exactly one `set_profile` event at clock zero.
Its payload contains `display_name`, `created_at`, and `schema_version`;
`created_at` equals its signed timestamp. New CLI profiles include it; the CLI's
first push upgrades legacy files using the matching private key.

| Operation | Payload used by the reference model |
| --- | --- |
| `set_profile` | `display_name`; registration also includes initial metadata |
| `set_topic` | `topic`, numeric `weight`, `visibility` |
| `remove_topic` | `topic` |
| `set_consent` | `field`, boolean `value` |
| `set_opt_out` | `topic`, boolean `value` |
| `recommend` | Recommendation record consumed by the local feed, not topic state |

Event IDs, profile IDs, device labels, and timestamps must be nonempty strings;
payloads must be objects. Parsers do not coerce signed fields before verifying them.
Topic weights and recommendation scores are finite JSON numbers in `[0, 1]`,
not numeric strings or booleans. Consent/opt-out values are JSON booleans;
consent fields are limited to `share_public_topics`, `ad_personalization`, and
`hosted_sync`. Recommendations require nonempty `item_id` and `site_id`, optional
object `metadata`, and default score `0.5` when omitted.
Invalid events fail with 400 on import/sync; batches are not partially stored.
Local event application validates before changing clocks or preference state.

Topic names are namespaced paths, such as `orf:technology/python`. Segments use
lowercase letters, digits, or hyphens; empty segments are invalid. The reference
validator also accepts Unicode lowercase letters/digits. ASCII names are the
portable baseline. There is no mandatory global taxonomy registry.

Hosted history is replayed in ascending `(clock, timestamp, event_id)` order:

- Topic set accepts an equal or higher clock; removal requires a strictly higher
  clock. Same-clock addition wins over removal.
- Same-clock consent revocation (`false`) wins over `true`.
- Same-clock opting out (`true`) wins over opting in.
- Other same-clock updates follow the replay order; later events in that order win.
- Unchanged retries are idempotent. Reusing an event ID for different content or
  another profile fails. Stale snapshots cannot delete newer hosted history.

Duplicate comparison uses the event's selected signing encoding. JCS numeric
variants such as `1.0` and `1` are equivalent; legacy variants are not.

History and rebuilt state commit atomically. Retained signed history is not
compacted, and removing a topic is not historical erasure.

## Visibility and scopes

| Topic visibility | Public projection | Consented site projection |
| --- | --- | --- |
| `public` | If `share_public_topics=true` and not opted out | Also requires `topics.public` |
| `selective` | Never | Exact `topics.selective:<topic-name>` scope, not opted out |
| `private` | Never | Never |

`profile.read` includes the display name; `consent.summary` includes
`share_public_topics` and `ad_personalization`. Projections always include the
profile ID and projection metadata. Neither projection exposes an opt-out list;
their topic lists exclude opted-out signals. Approved scopes can still name topics
the site requested, without revealing whether they are opted out. Consent flags
are not permission to bypass topic/scope checks.

**Privacy migration:** public projections no longer contain `opt_out_topics`,
for either legacy 0.1.0 or new 0.2.0 profiles. Clients must not require that field
or infer an opt-in from its absence. This affects public GET, registration/append
responses, CLI public export, and the site demo; owner files and signed sync history
are unchanged. Opt-outs suppress sharing those topic signals; they do not filter
a site's candidate catalog or erase previously delivered copies.

Sites request `required_scopes` and `optional_scopes`. Unknown required scopes
fail; unknown optional scopes are reported and ignored. Legacy `requested_scopes`
is treated as optional. Do not send both forms. Approval must include all required
scopes and must be a nonempty subset of the recognized requested scopes.

The full profile and event-sync surfaces contain private history. These are not
substitutes for a projection and must not be used as partner-personalization APIs.

## Owner actions

1. `POST /profiles/{profile_id}/owner-action-challenges` with
   `{action, target_id, parameters}`.
2. The user's trusted signer signs the complete returned `challenge_payload`.
3. Submit unchanged parameters plus
   `owner_proof: {challenge_id, signature}` to the corresponding endpoint.

| Action | Target | Endpoint | Parameters |
| --- | --- | --- | --- |
| `approve` | Request ID | `/site-access-requests/{id}/approve` | `approved_scopes`, `grant_expires_at`, `actor` |
| `deny` | Request ID | `/site-access-requests/{id}/deny` | `reason`, `actor` |
| `revoke` | Grant ID | `/grants/{id}/revoke` | `reason`, `actor` |
| `delete` | Profile ID | `/profiles/{id}/delete` | None; `{}` |
| `sync-read` | Profile ID | `/profiles/{id}/events/read` | Required `after_clock`: non-negative safe integer |

The challenge type is `owner-<action>:<digest>`, where `digest` is lowercase
SHA-256 hex of the canonical JSON of `{action, target_id, parameters}`. It binds
the exact parameter object, not an approximation of the decision. Unknown
parameters fail; `actor` is a nonempty string when present; reasons/expiry may be
strings or null. An explicit empty `approved_scopes` array fails.

Challenges expire after **five minutes** and can be consumed once. The service
looks up the target owner and verifies with that profile's key. Proof consumption
and the action share one transaction; an invalid action rolls back consumption.
Missing proof returns 401; invalid, expired, or replayed proof returns 403;
invalid action semantics return 400; unknown targets return 404.
Local browser mutation routes additionally require localhost and a review token.
Sync reads bind the requested cursor just like a mutation binds its parameters;
each read needs a fresh proof. Verification, consumption, and history selection
use one transaction. The read audit stores cursor/count, not the event payloads.
Personal responses and challenge/consent surfaces use `Cache-Control: no-store`.
Only public-profile GET responses are exempt; this is not permission for a site
to retain or cache private history.

## Partner authentication

The registry limits permitted scopes; a `site_id` alone is not proof of identity.
When `OPEN_RECOMMENDER_SITE_TOKEN_HASHES` is configured, partner calls require
exactly one `X-ORF-Site-ID` and one `X-ORF-Site-Token` header. The server compares
SHA-256 of the UTF-8 token with that site's configured lowercase hex digest.
Use a distinct random token with at least 32 random bytes per site, held only by
its backend. These headers are separate from sync/admin tokens and owner proofs.

This gate covers request creation, request-status GET, exchange, verification,
projection, ranking, and feedback. Missing/invalid/duplicate credentials return
401. Creating a request for a different site returns 403. Another site's request
or session returns 404, like an unknown ID, before exchange/projection/feedback
side effects. A valid site token never approves a request or authorizes raw history.
Public projections and owner-proof endpoints are separate and unchanged.

Unset configuration retains unauthenticated localhost-preview behavior. An empty
object denies all partner calls; malformed config, JSON `null`, unknown site IDs,
or reused token hashes fail startup. Health reports `site_auth_required`, never
hashes or tokens. This is a reference-service gate, not an interoperable identity
provider or proof of a site's domain. See [backend setup](pilot-integration.md#backend-credentials).

## Grant exchange and use

After approval, call `/site-access-requests/{id}/exchange`. The response contains
a `grant-exchange` challenge bound to the profile, request, site, and grant.
Have the user sign it, then POST `challenge_id` and `signature` to
`/site-access-requests/{id}/verify`. A successful verification returns a session.

Default grant life is seven days; default session life is thirty minutes.
Caller-specified expiries are supported. A session is a **bearer credential**;
with partner authentication enabled, use additionally requires credentials for
that session's site. Every read/use checks parent-grant expiration or revocation.

- `GET /grant-sessions/{id}/projection` returns approved topics/metadata.
- `POST /grant-sessions/{id}/rank` accepts site-owned candidates with
  `candidate_id`, `site_score`, optional `candidate_topics`, `published_at`, and
  `metadata`; request options are `top_n`, `include_debug`, and `schema_version`.
- Ranking returns scores, ranks, reason codes, and echoed site metadata, not raw
  user topic weights. It is a baseline reranker, not a universal recommender.
- `/rank/feedback` accepts `click`, `dismiss`, or `save` events with a dedupe
  `event_id` and candidate ID. Feedback stays grant-local in the hosted service;
  it does not mutate or travel with the portable profile.

Ranking candidates and feedback events must be objects. IDs are nonempty strings;
candidate IDs are unique within a ranking request. `site_score` is a finite JSON
number in `[0, 1]`. Topic arrays contain nonempty strings and metadata is an object
with finite JSON values. Malformed ranking/feedback requests return 400; invalid
feedback batches store no events. `top_n` is a positive integer and `include_debug`
is a boolean. Missing/invalid outer request bodies may return the framework's 422.

Revocation blocks further service use, not in-flight responses or downloaded
copies. Deletion removes live profile-linked history, grants, sessions, challenges,
feedback, and audit rows atomically. Backups/logs, partner copies, and local files
are outside that operation. No tombstone prevents later signed re-registration.

## Sync boundaries

`POST /profiles` merges a profile's signed history. `POST /profiles/{id}/events`
appends signed events. Raw reads are **owner-only**:

1. Request an owner-action challenge with `action: "sync-read"`, the profile ID
   as `target_id`, and `parameters: {after_clock: N}` (N is required).
2. Sign the returned challenge in the user's trusted client.
3. `POST /profiles/{id}/events/read` with `{after_clock: N, owner_proof: ...}`.

The response is `{profile_id, events}`; events have clocks **strictly greater**
than N and are ordered by `(clock, timestamp, event_id)`. Zero excludes registration.
The optional service-wide sync token is an additional Bearer gate on this request.
It never substitutes for owner proof. The old `GET /profiles/{id}/events` is retired
and returns 410 (or 401 if its configured token gate is not satisfied); it returns no history.

**Migration:** SDK pulls now require `ownerProof` / `owner_proof`. The CLI loads
the matching key for `sync-pull`. A partner site must use scoped projections or
ranking, not obtain an owner's raw-history proof.

A clock alone is not a complete cursor for late, lower-clock or same-clock events.
The CLI therefore requests all post-registration events (`after_clock: 0`),
merges them with retained local history by event ID, and replays the verified union
in the same deterministic order as the service. This preserves unsent local
changes and catches late events without relying on device clocks as a cursor.
It costs bandwidth proportional to history size; there is no pagination or
server-sequence cursor yet. The local signed registration must already be present.
SDK callers choosing a higher clock must not treat it as complete replication.
The CLI rejects forged batches and conflicting event IDs without writing its
local file. Signatures prove authenticity, not that the service returned every event.

## Service budgets

These are configurable reference-service budgets, not maximum sizes in the ORF
file format. `GET /health` reports them under `service.limits`.

| Health field | Default | Applies to |
| --- | --- | --- |
| `max_request_bytes` | 2 MiB | Received JSON body bytes, including chunked uploads |
| `max_profile_events` | 10,000 | Incoming profile/event lists and retained events per profile, including registration |
| `max_ranking_candidates` | 500 | Candidates in one ranking request |
| `max_feedback_batch` | 500 | Feedback events in one request |
| `max_grant_feedback_events` | 10,000 | Retained feedback events per grant |
| `max_history_bytes` | 8 MiB | Stored event JSON bytes, independently per profile and per grant feedback history |

Excess work returns **413**. History budgets are checked in the write transaction;
the entire rejected batch rolls back. Valid identical retries do not consume new
history slots or bytes, but must still fit incoming request/item budgets.
Histories already exceeding a newly lowered budget are not trimmed automatically.

Split oversized candidate or feedback request batches if useful, but splitting
does not bypass a cumulative history budget. Do not drop or edit signed user events
to fit a service limit. Keep the local file intact and arrange appropriate capacity
with the operator or another trusted service. Full profile uploads may hit the
request-body budget before the retained-history budget; there is no automatic
chunked-profile import or history compaction protocol yet.

Protected routes can also return **429** with `Retry-After` in seconds. Profile
registration/update, event append, and Lens import share a per-client write bucket.
Wait before retrying; a site should keep its normal fallback feed when ranking is
unavailable. A 413 is not a transient rate limit and should not be retried unchanged
in a tight loop. Operator configuration is in [Architecture](architecture.md#storage-and-operation).

## Compatibility

Versions use `major.minor.patch`; the implementation currently accepts matching
major versions. Contract helpers preserve additive fields in `extra_fields`;
profile/event helpers do not promise that round trip. Unknown event operations
and unknown owner-action parameters are rejected. Unknown optional scopes must
not silently become permission.

All current versions are experimental major zero. Acceptance of a version does
not certify independent-client compatibility. Keep signed envelopes byte-compatible,
test against the vectors, and treat privacy/auth changes as compatibility-sensitive.
Older clients that relied on numeric-string coercion, truthy consent values, or
non-finite numbers must submit correctly typed JSON. Do not edit signed payloads
in place to repair them: signatures bind their original values.
