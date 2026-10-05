# @open-recommender/orf-web-sdk

A tiny browser-friendly ESM client for the [Open Recommender](../../README.md) hosted ORF service.

No Node.js APIs. Uses standard browser APIs. Works in React, Vite, or any modern bundler.

For the new local-first Mac/Chrome login path (also shared with Safari), use `connectNativeORF({nonce,
expiresAt})` directly from a click handler, with a backend-issued challenge.
It talks to the extension, not the hosted service, and never loads a profile/key.
Backend proof verification and atomic challenge consumption are mandatory.
See the [native gateway guide](../../docs/native-gateway.md); this is a login-only
preview, not a replacement for the hosted methods below.

Direct partner calls in this guide target the unauthenticated localhost preview.
When service-side partner authentication is enabled, route those calls through
your site's backend instead. This browser SDK deliberately has no backend-token
option: never expose site credentials in browser code. See
[backend credentials](../../docs/pilot-integration.md#backend-credentials).

Profile uploads must contain the signed clock-zero registration event created by the
current CLI. Upgrade older `.orf` files with `sync-push` and their matching key before
calling `upsertProfile`. Uploads merge verified events and cannot replace hosted history.
Registration sends full state, including private topics, to the trusted service.
For a file import, pass the original JSON text to `upsertProfile`, not
`JSON.parse(fileText)`: reserializing Python numbers like `1.0` as `1` can
invalidate legacy event signatures, including mixed 0.1.0 histories. New 0.2.0
events use JCS and survive these number round trips. Object input remains available for callers
that already control the signed event representation.

## Install (from local path while in monorepo)

```sh
# From the react-sample-app or any JS project in this repo:
npm install ../../sdk/orf-web-sdk
```

## Usage

```js
import { ORFClient, canonicalJsonBytes, encodeBase64Url } from "@open-recommender/orf-web-sdk";

const client = new ORFClient("http://127.0.0.1:8000");

// 1. Register or update a local profile in the ORF service from the browser
await client.upsertProfile(await profileFile.text());

// 2. Create a site access request
const created = await client.createAccessRequest({
  profileId: "orf:profile:...",
  siteId: "open-news-demo",
  purpose: "Personalize the pilot site feed.",
  requiredScopes: ["profile.read", "topics.public"],
  optionalScopes: ["topics.selective:orf:media/podcasts"],
});
const requestId = created.access_request.request_id;
const consentUrl = created.consent_review_url;

// 3. For localhost demo flows, render consent inline in the browser
const review = await client.getConsentReview(requestId);
const approvedScopes = review.scope_groups.already_public.map((item) => item.scope);
const { challenge_payload: ownerChallenge } = await client.createOwnerActionChallenge({
  profileId: created.profile_id,
  action: "approve",
  targetId: requestId,
  parameters: { approved_scopes: approvedScopes },
});
// userSigningKey is loaded only in the user's trusted client, never in a partner backend.
const ownerSignature = encodeBase64Url(new Uint8Array(await crypto.subtle.sign(
  "Ed25519", userSigningKey, canonicalJsonBytes(ownerChallenge),
)));
const approval = await client.approveConsentRequest({
  requestId,
  approvedScopes,
  csrfToken: review.csrf_token,
  ownerProof: { challenge_id: ownerChallenge.challenge_id, signature: ownerSignature },
});

// 4. Poll request status
const status = await client.getAccessRequest(requestId);

// 5. After user approves, start the exchange
const exchange = await client.startExchange(requestId);

// 6. Sign challenge_payload with the user's ORF key (user-side — not the site!),
//    then verify:
const verified = await client.verifySignature({
  requestId,
  challengeId: exchange.challenge.challenge_id,
  signature: "<base64url-ed25519-sig>",
});

// 7. Read the consented projection
const projection = await client.getProjection(verified.session.session_id);
console.log(projection.projection.topics);

// 8. Rerank site-owned candidates inside the verified grant session
const ranking = await client.rankCandidates(verified.session.session_id, {
  topN: 2,
  candidates: [
    {
      candidate_id: "story-123",
      site_score: 0.78,
      candidate_topics: ["orf:media/podcasts"],
      metadata: { slot: "hero" },
    },
    {
      candidate_id: "story-456",
      site_score: 0.81,
      candidate_topics: ["orf:technology/python"],
      metadata: { slot: "secondary" },
    },
  ],
});
console.log(ranking.ranking.ranked_candidates);
```

## API

### `new ORFClient(baseUrl, options?)`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `baseUrl` | `string` | — | Base URL of the running ORF service |
| `options.syncToken` | `string` | `null` | Optional additional Bearer gate; never replaces owner proof for a read |

### Methods

Public projections omit `opt_out_topics`, including for legacy profiles. Do not
require that field or interpret its absence as an opt-in. Opt-outs remain in owner
history, not partner responses; see the [privacy migration](../../docs/protocol.md#visibility-and-scopes).

| Method | Description |
|--------|-------------|
| `upsertProfile(profile)` | Register a document; pass original file text to preserve signed numbers |
| `createAccessRequest({ profileId, siteId, purpose, requestedScopes?, requiredScopes?, optionalScopes?, expiresAt? })` | Create a site access request using either the legacy combined scope list or explicit required/optional scope tiers |
| `getAccessRequest(requestId)` | Get current request state |
| `getConsentReview(requestId)` | Read localhost consent review data for a pending request |
| `createOwnerActionChallenge({ profileId, action, targetId, parameters? })` | Request an owner challenge bound to the exact action/target/parameters |
| `approveConsentRequest({ requestId, approvedScopes?, csrfToken, ownerProof })` | Approve a localhost request with a signed owner proof and review token |
| `denyConsentRequest({ requestId, reason?, csrfToken, ownerProof })` | Deny a localhost request with a signed owner proof and review token |
| `revokeGrant(grantId, { reason?, ownerProof })` | Revoke a grant with a proof bound to the same parameters |
| `deleteProfile(profileId, ownerProof)` | Delete live hosted rows with a proof for `delete` and parameters `{}` |
| `startExchange(requestId)` | Begin the challenge exchange (request must be approved) |
| `verifySignature({ requestId, challengeId, signature, sessionExpiresAt? })` | Verify the signed challenge to get a grant session |
| `getProjection(sessionId)` | Fetch the consented projection |
| `rankCandidates(sessionId, { candidates, topN?, includeDebug?, schemaVersion? })` | Rerank site-generated candidates inside a verified grant session |
| `getPublicProfile(profileId)` | Read the public profile (no auth) |
| `pushEvents(profileId, events)` | Push signed sync events |
| `pullEvents(profileId, { afterClock?, ownerProof })` | Owner-only history read with a proof bound to that exact cursor |

### `canonicalJsonBytes(payload)` / `encodeBase64Url(bytes)`

Utility helpers for producing the base64url-encoded Ed25519 signature over `challenge_payload`.
The encoder matches the reference service for challenges: ASCII field names, strings
(including Unicode), booleans, null, arrays, objects, and safe integers. It rejects
floating-point numbers, negative zero, and unsupported values rather than producing
incompatible signatures. It is **not a general signed-event encoder**; legacy Python
events may contain floats whose serialization differs in JavaScript. See the
[wire contract and shared test vectors](../../docs/protocol.md#signatures).
**Signing itself must happen in the user's client**, not in a site's backend or normal site code.
The React sample app shows one localhost-friendly pattern: load the user's `.orf.key` into browser
memory with Web Crypto, sign locally, and send only the signature in `owner_proof` or
to `verifySignature(...)`. Owner proofs expire after five minutes and are one-time use;
the final request must contain the exact parameters used when requesting the challenge.
Deletion does not delete local files, operator backups/logs, or partner copies.

### `canonicalEventJsonBytes(unsignedEvent)`

Encode new events with the signed `signature_encoding: "jcs-rfc8785"` marker.
Only the user's **separate, trusted signing client** should do this; a partner
page or backend must never receive the user's key.

```js
import { canonicalEventJsonBytes } from "@open-recommender/orf-web-sdk";

const unsignedEvent = {
  event_id: crypto.randomUUID(), profile_id: profileId, device_id: deviceId,
  clock: nextClock, timestamp: new Date().toISOString(), op: "set_topic",
  payload: { topic: "orf:technology/python", weight: 0.7, visibility: "private" },
  signature_encoding: "jcs-rfc8785",
};
const signature = encodeBase64Url(new Uint8Array(await crypto.subtle.sign(
  "Ed25519", userSigningKey, canonicalEventJsonBytes(unsignedEvent),
)));
await client.pushEvents(profileId, [{ ...unsignedEvent, signature }]);
```

The encoder accepts plain JSON with finite binary64 numbers and valid Unicode.
It rejects missing/unknown markers, lone surrogates, unsupported values, and an
outer `signature` field. Use strings for exact large identifiers. It does not
allocate clocks, validate operation payloads, or verify history. New 0.2.0 profiles
require marked events throughout; existing 0.1.0 signatures must not be rewritten.
See the [event vectors](../../tests/fixtures/event-signing-vectors.json) and
[encoding rules](../../docs/protocol.md#jcs-event-encoding).

## Owner-only sync reads

Raw history includes private topics and must not be used by a partner site.
In the user's trusted client, get and sign a fresh read challenge for each pull:

```js
const afterClock = 0;
const { challenge_payload: challenge } = await client.createOwnerActionChallenge({
  profileId, action: "sync-read", targetId: profileId,
  parameters: { after_clock: afterClock },
});
const signature = encodeBase64Url(new Uint8Array(await crypto.subtle.sign(
  "Ed25519", userSigningKey, canonicalJsonBytes(challenge),
)));
const result = await client.pullEvents(profileId, {
  afterClock, ownerProof: { challenge_id: challenge.challenge_id, signature },
});
```

Verify signatures with each event's selected encoding, then validate identity,
registration, clocks, and replay rules before applying history. Neither encoder
alone is a history verifier; the challenge encoder cannot encode floating-point events.
This uses `POST /profiles/{id}/events/read`; the old raw GET is retired.

## Scope tiers

The recommended request shape is:

- **Required scopes** — all must be approved for the request to continue
- **Optional scopes** — the user can drop these and still approve the request

Legacy `requestedScopes` still works and is treated as one combined optional list for backward
compatibility.

### `ORFClientError`

Thrown by all methods on failure.

| Property | Type | Description |
|----------|------|-------------|
| `message` | `string` | Human-readable message |
| `status` | `number\|null` | HTTP status code or `null` for network errors |
| `detail` | `unknown` | `detail` field from the service error response, if any |

## Tests

```sh
npm test
```

This runs the Node-based unit tests for helper behavior and request shaping.
