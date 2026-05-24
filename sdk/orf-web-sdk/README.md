# @open-recommender/orf-web-sdk

A tiny browser-friendly ESM client for the [Open Recommender](../../README.md) hosted ORF service.

No Node.js APIs. Uses only the browser-native `fetch` API. Works in React, Vite, or any modern bundler.

## Install (from local path while in monorepo)

```sh
# From the react-sample-app or any JS project in this repo:
npm install ../../sdk/orf-web-sdk
```

## Usage

```js
import { ORFClient } from "@open-recommender/orf-web-sdk";

const client = new ORFClient("http://127.0.0.1:8000");

// 1. Register or update a local profile in the ORF service from the browser
await client.upsertProfile(profileDocument);

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
const approval = await client.approveConsentRequest({
  requestId,
  approvedScopes: review.scope_groups.already_public.map((item) => item.scope),
  csrfToken: review.csrf_token,
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
```

## API

### `new ORFClient(baseUrl, options?)`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `baseUrl` | `string` | — | Base URL of the running ORF service |
| `options.syncToken` | `string` | `null` | Bearer token for sync push/pull if the service requires auth |

### Methods

| Method | Description |
|--------|-------------|
| `upsertProfile(profile)` | Register or update a profile document in the ORF service |
| `createAccessRequest({ profileId, siteId, purpose, requestedScopes?, requiredScopes?, optionalScopes?, expiresAt? })` | Create a site access request using either the legacy combined scope list or explicit required/optional scope tiers |
| `getAccessRequest(requestId)` | Get current request state |
| `getConsentReview(requestId)` | Read localhost consent review data for a pending request |
| `approveConsentRequest({ requestId, approvedScopes?, csrfToken })` | Approve a localhost consent request from the browser |
| `denyConsentRequest({ requestId, reason?, csrfToken })` | Deny a localhost consent request from the browser |
| `startExchange(requestId)` | Begin the challenge exchange (request must be approved) |
| `verifySignature({ requestId, challengeId, signature, sessionExpiresAt? })` | Verify the signed challenge to get a grant session |
| `getProjection(sessionId)` | Fetch the consented projection |
| `getPublicProfile(profileId)` | Read the public profile (no auth) |
| `pushEvents(profileId, events)` | Push signed sync events |
| `pullEvents(profileId, { afterClock? })` | Pull sync events since a clock value |

### `canonicalJsonBytes(payload)` / `encodeBase64Url(bytes)`

Utility helpers for producing the base64url-encoded Ed25519 signature over `challenge_payload`.
**Signing itself must happen in the user's client**, not in a site's backend or normal site code.
The React sample app shows one localhost-friendly pattern: load the user's `.orf.key` into browser
memory with Web Crypto, sign locally, and send only the signature to `verifySignature(...)`.

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
