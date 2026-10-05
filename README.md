# Open Recommender

**Bring your preferences. Choose what to share. Keep the ability to leave.**

Open Recommender is a Python reference implementation of **ORF — Open Recommender
Format**: portable, signed preference profiles and scoped access for websites.
Users carry a readable `.orf` file; sites request specific signals rather than
owning another isolated preference history.

Sites can use those signals in their existing recommender, or send their own
candidates to the included reranking API. The ranking algorithm is replaceable;
the user-control and interoperability contracts are the core of the project.

## Status

**Developer preview, for local demos and controlled pilots.** Not a production
identity provider, encrypted sync service, or proven web-scale deployment.
The Python package is 0.1.0; profile and access-contract versions are separate.
New profiles use 0.2.0 with cross-language JCS event signatures. Existing 0.1.0
history remains readable without changing its signatures.
See the [wire contract](docs/protocol.md) and [architecture](docs/architecture.md).

A separate [Chrome-first Mac native gateway preview](docs/native-gateway.md) starts
the local-first architecture: native approval and site-specific login keys,
without uploading a profile. The unpacked Chrome extension and Swift native host
are buildable without a signing team; native UI/Keychain installation still needs
manual validation. It does not replace the hosted flow or import `.orf` yet.

There is currently no repository license. Release licensing is not yet finalized;
do not assume permission to redistribute or integrate it as an open-source dependency.

## What works

- Portable JSON profiles, Ed25519 identity, signed mutations, deterministic merge.
- Public, selective, and private topic visibility; explicit topic opt-outs.
- Signed owner approval, denial, revocation, and hosted deletion.
- Short-lived grant sessions for scoped projections, reranking, and site-local feedback.
- Optional per-site backend authentication, separate from user approval and owner sync.
- CLI, Python partner client, dependency-free browser SDK, and React demo.
- Owner-authenticated hosted history reads, local feed aggregation, and backups with encrypted recovery keys.

**The privacy boundary:** private topics stay out of partner projections, but
registration and sync upload full readable history to your chosen service.
Public projections omit opted-out topic names. Backups encrypt the key, not
the profile. Revocation blocks further service access, not copies already held
by a site. Read [Transparency & Security](docs/transparency-and-security.md).

## Try it locally

Requires Python 3.10+. From a cloned checkout:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m open_recommender.cli create profile.orf --display-name "Alice" --device-id laptop
python -m open_recommender.cli topic-set profile.orf orf:technology/python 0.9
python -m open_recommender.cli topic-set profile.orf orf:media/podcasts 0.8 --visibility selective
python -m open_recommender.cli export-public profile.orf
```

On Windows, activate with `.venv\Scripts\activate`. Keep `profile.orf.key`
private; the profile file is readable and the key controls this identity.

Start the local service in this terminal:

```sh
python -m uvicorn open_recommender.service:create_app --factory --host 127.0.0.1
```

In a second terminal with the same environment activated:

```sh
python examples/pilot_dry_run.py http://127.0.0.1:8000
```

The narrated dry-run creates a test profile, signs consent, exchanges a grant,
reads its projection, syncs changes, and checks that revocation blocks the old
session. It uses synthetic data, not your `profile.orf`.
For authenticated Python examples, follow the [backend credential setup](docs/pilot-integration.md#backend-credentials).
They read `ORF_SITE_TOKEN` from protected environment configuration, never browser code.

## See the browser demo

With the local service running, in another terminal (Node.js 22.12+ required):

```sh
cd sdk/react-sample-app
npm ci
npm run dev
```

Open [localhost:5173](http://localhost:5173), choose your `profile.orf` and its
matching unencrypted `profile.orf.key`, review the sharing request, then sign in
and rerank the sample feed. Selecting the profile uploads it to the local service;
selecting the key imports it into this tab's memory.

This combined user/site demo is **localhost-only**. Real sites must never ask
users to upload their private key. The [React guide](sdk/react-sample-app/README.md)
explains the trust boundary; [Local Profile Lens](http://127.0.0.1:8000/lens) lets
you inspect a file before choosing whether to register it.

## Integrate a site

Keep candidate generation and your normal fallback feed. Request the smallest
useful scope set, get the user's signed approval, and use the verified session
to read a projection or rerank candidates. Do not read raw sync history from
site code: it includes private data.

Start with the [integration guide](docs/integration-guide.md), then use the
[Python partner client](docs/pilot-integration.md) or
[browser SDK](sdk/orf-web-sdk/README.md). The running API exposes route docs at
[localhost:8000/docs](http://127.0.0.1:8000/docs).
SDK installation is currently from this checkout, not a published npm release.

## Develop and verify

```sh
python -m unittest discover -s tests -v
cd sdk/orf-web-sdk
npm test
cd ../react-sample-app
npm ci
npm test
```

The browser test builds the demo and runs headless Chromium. Shared
[challenge vectors](tests/fixtures/signing-vectors.json) and
[JCS event vectors](tests/fixtures/event-signing-vectors.json) are signed and
verified in Python and JavaScript, including floating-point event metadata.
The packaged [profile/event schema](src/open_recommender/schemas/profile.schema.json)
checks structure, not signatures or authorization; see its
[validation limits](docs/protocol.md#machine-readable-structure).

Compare the baseline reranker with site scores using the local
[ranking evaluation](docs/ranking-evaluation.md). Its bundled dataset is synthetic,
not evidence of real-world recommendation quality.

## Documentation

| I want to… | Read |
| --- | --- |
| Create, sync, recover, revoke, or delete a profile | [Getting started](docs/getting-started.md) |
| Understand bytes, scopes, versions, and replay rules | [Protocol](docs/protocol.md) |
| Understand components and deployment boundaries | [Architecture](docs/architecture.md) |
| Add a site without replacing its recommender | [Integration guide](docs/integration-guide.md) |
| Measure the reranker against site scores | [Ranking evaluation](docs/ranking-evaluation.md) |
| Inspect privacy and retention limits | [Transparency & Security](docs/transparency-and-security.md) |
| Run a detailed pilot walkthrough | [Pilot integration](docs/pilot-integration.md) |
| Explore the local cross-site feed | [Cross-site feed](docs/cross-site-feed.md) |
| Contribute code or docs | [Contributing](CONTRIBUTING.md) |

Core code lives in `src/open_recommender/`; browser packages in `sdk/`;
runnable site integrations in `examples/`; regression tests in `tests/`.
