# orf-react-sample-app

A minimal Vite + React demo that exercises the [`@open-recommender/orf-web-sdk`](../orf-web-sdk/README.md) browser SDK against a locally running ORF service.

## Prerequisites

- Node.js 22.12+ required by the build/browser tooling
- The Open Recommender FastAPI service running on `http://127.0.0.1:8000`
- An Open Recommender Format (ORF) profile file like `profile.orf`
- The matching `profile.orf.key` file if you want the browser demo to finish sign-in end to end

Start the service (from the repo root):

```sh
./.venv/bin/python -m uvicorn open_recommender.service:create_app --factory --reload
```

Register a profile with the local service before using the demo:

```sh
python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000
```

Or open `http://127.0.0.1:8000/lens`, load the local `.orf` file, and click **Register or update in local service**.

The browser demo can also register the profile directly when you upload the local `.orf` file there, so pre-registration is optional.

Browser imports require the signed clock-zero registration event present in new CLI
profiles. For older files, run `sync-push` with the matching key first, then upload the
upgraded file. Profile registration sends full state and history, including private
topics, to the trusted local service; projections restrict what the partner site sees.

## Install and run

```sh
cd sdk/react-sample-app
npm ci
npm run dev
```

Open `http://localhost:5173` in your browser.

The local ORF service must be running, and it already allows browser requests from localhost origins like this demo.

## What this demo shows

1. **Upload `.orf` from the browser or enter a profile ID** — the app can register a local profile with `client.upsertProfile(…)`, or it can use an already-registered profile ID.
2. **Create the request** — the app calls `client.createAccessRequest(…)` with a required baseline (`profile.read`, `topics.public`) plus one optional selective topic.
3. **Review and sign consent inline** — load the matching unencrypted `.orf.key` first. The app shows required/optional scopes, signs a one-time challenge bound to the exact approval or denial, and submits only the signature plus decision parameters and review token.
4. **Reuse prior approval** — if the backend already has an active grant for the same profile, site, purpose, and scopes, the request comes back already approved instead of asking again.
5. **Start exchange** — once approved, click to call `client.startExchange(…)`. The challenge ID is displayed.
6. **Finish sign-in in the browser** — upload the matching unencrypted PKCS#8 PEM `.orf.key` file. The demo imports it with Web Crypto, signs `challenge_payload` in-tab, and sends only the signature to `client.verifySignature(…)`.
7. **Projection** — after a successful `client.verifySignature(…)` + `client.getProjection(…)` call, the consented projection is rendered in the same browser flow.
8. **Rerank the sample feed** — the demo immediately calls `client.rankCandidates(…)` with a fixed set of site-owned candidates so the flow continues into a realistic recommendation handoff instead of stopping at projection.

## Local key boundary

- The sample app never uploads the private key itself to the ORF service.
- The imported key stays in browser memory for the current tab only.
- The current browser flow expects an **unencrypted** PKCS#8 PEM key file. Encrypted `.orf.key` handling is not implemented in this sample yet.

Keep real profiles, backups, and keys outside the app directory. The development
server binds to loopback and denies `.orf`, `.orfb`, and `.key` files in addition
to Vite's default sensitive-file patterns. Unfiltered `public/` file copying is
disabled, and browser console forwarding is off. These are guardrails, not a
sandbox for site-controlled code or arbitrary renamed sensitive files.

## Build the static demo

```sh
npm run build
```

Output goes to `dist/`. Use `npm run preview` for local inspection. A successful
build does not make the combined key-upload/partner demo safe to deploy publicly.
Real partner sites must keep the user's signer outside site-controlled code.

## Tests

```sh
npm test
```

This runs Puppeteer's bundled headless-shell browser against a local mock ORF API and the built React app. It covers
browser-side profile import, request creation, inline consent approval, exchange start, projection
handoff, grant-session reranking, wrong-key failure/recovery, and mobile overflow.
The mock verifies real Ed25519 signatures for both consent and grant exchange.
The suite also probes the actual development server with synthetic sensitive
files and verifies that ordinary and raw-file requests receive 403 responses.

The consent approval mock verifies the actual Ed25519 owner signature, action, target,
and parameters, and consumes the challenge once. This checks browser signing and SDK
request shaping; Python integration tests cover the real service's authorization,
replay protection, revocation, and deletion transactions.

The test also writes desktop/mobile screenshots to the operating system's temp
directory (`orf-demo-desktop.png` and `orf-demo-mobile.png`) for visual inspection.

## SDK note

The app uses `@open-recommender/orf-web-sdk` from the local `../orf-web-sdk` path. The SDK is a zero-dependency ESM module using only the browser `fetch` API — no Node.js specific code.
