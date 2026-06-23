# orf-react-sample-app

A minimal Vite + React demo that exercises the [`@open-recommender/orf-web-sdk`](../orf-web-sdk/README.md) browser SDK against a locally running ORF service.

## Prerequisites

- Node.js ≥ 18
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

## Install and run

```sh
cd sdk/react-sample-app
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

The local ORF service must be running, and it already allows browser requests from localhost origins like this demo.

## What this demo shows

1. **Upload `.orf` from the browser or enter a profile ID** — the app can register a local profile with `client.upsertProfile(…)`, or it can use an already-registered profile ID.
2. **Create the request** — the app calls `client.createAccessRequest(…)` with a required baseline (`profile.read`, `topics.public`) plus one optional selective topic.
3. **Review consent inline** — for localhost demo flows, the app renders the ORF consent review data inline in the browser and shows required scopes separately from optional ones.
4. **Reuse prior approval** — if the backend already has an active grant for the same profile, site, purpose, and scopes, the request comes back already approved instead of asking again.
5. **Start exchange** — once approved, click to call `client.startExchange(…)`. The challenge ID is displayed.
6. **Finish sign-in in the browser** — upload the matching unencrypted PKCS#8 PEM `.orf.key` file. The demo imports it with Web Crypto, signs `challenge_payload` in-tab, and sends only the signature to `client.verifySignature(…)`.
7. **Projection** — after a successful `client.verifySignature(…)` + `client.getProjection(…)` call, the consented projection is rendered in the same browser flow.
8. **Rerank the sample feed** — the demo immediately calls `client.rankCandidates(…)` with a fixed set of site-owned candidates so the flow continues into a realistic recommendation handoff instead of stopping at projection.

## Local key boundary

- The sample app never uploads the private key itself to the ORF service.
- The imported key stays in browser memory for the current tab only.
- The current browser flow expects an **unencrypted** PKCS#8 PEM key file. Encrypted `.orf.key` handling is not implemented in this sample yet.

## Building for production

```sh
npm run build
```

Output goes to `dist/`. Serve with `npm run preview` or any static file host.

## Tests

```sh
npm test
```

This runs a Puppeteer smoke test against a local mock ORF API and the built React app. It covers
browser-side profile import, request creation, inline consent approval, exchange start, projection
handoff, and grant-session reranking of the sample feed. The mock API still treats signature
verification as a stubbed success path.

## SDK note

The app uses `@open-recommender/orf-web-sdk` from the local `../orf-web-sdk` path. The SDK is a zero-dependency ESM module using only the browser `fetch` API — no Node.js specific code.
