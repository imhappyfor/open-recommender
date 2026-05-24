# sdk/

This directory contains the browser-facing integration layer for Open Recommender.

| Package | Description |
|---------|-------------|
| [`orf-web-sdk/`](orf-web-sdk/README.md) | Zero-dependency ESM browser SDK — wraps the ORF service HTTP API using `fetch`. Works in React, Vite, and any modern browser. |
| [`react-sample-app/`](react-sample-app/README.md) | Minimal Vite + React demo app that exercises the SDK end-to-end against a locally running ORF service. |

## Quick start

```sh
# Start the ORF service (from repo root)
./.venv/bin/python -m uvicorn open_recommender.service:create_app --factory --reload

# Install and run the React demo
cd sdk/react-sample-app
npm install
npm run dev
# → http://localhost:5173
```

The reference service accepts requests from local browser origins such as `http://localhost:5173` and `http://127.0.0.1:5173`.

See each package README for full usage details.

## Tests

- `sdk/orf-web-sdk/`: `npm test`
- `sdk/react-sample-app/`: `npm test`
