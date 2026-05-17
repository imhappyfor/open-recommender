# Open Recommender

Open Recommender is an early Python implementation of portable, user-controlled recommender profiles. Your recommendation taste is yours to carry across the internet.

## The Core Idea

Think of your recommendation profile like a credit score — one portable, verifiable record that travels with you. Except unlike a credit score, *you own it completely*. You decide what parts are public, what stays private, and which sites get to see what.

- **You own your data**: Your `.orf` profile lives on your device or a service of your choice.
- **You control sharing**: Mark topics as public, selective, or private. Sites see only what you approve.
- **You carry it forward**: Move to a new platform? Bring your preferences with you. No cold start. No algorithmic amnesia.
- **It's auditable**: Open the file in a text editor. See exactly what's stored. No hidden data.

Today the repository includes:

- an ORF profile model for portable preference data
- Ed25519 keys and signed sync events
- a FastAPI service for hosted profile sync and public profile reads
- an API-first demo flow for instant profile-based personalization and proof-of-control
- a site-scoped consented access flow for manually registered pilot sites
- a localhost-only browser consent review page for pending access requests
- a localhost-only browser trust app with a consent inbox and Local Profile Lens
- a local CLI for creating, editing, exporting, syncing, and resolving pilot access requests
- backup and restore CLI commands for portable profile + key recovery
- a thin partner SDK module for site-side request, exchange, verify, and projection calls
- pilot-safe service basics: auth-route rate limiting plus admin audit inspection endpoints
- unit tests for merge rules, privacy boundaries, signatures, and hosted API flows

## What ORF means here

An ORF profile is a signed JSON document that keeps user preference state local and portable. The current model stores:

- profile identity derived from an Ed25519 public key
- topic preferences with `public`, `selective`, or `private` visibility
- topic opt-outs
- consent flags such as `share_public_topics`, `ad_personalization`, and `hosted_sync`
- an append-only signed event log used for sync and conflict resolution

Public projections intentionally expose less than the full profile: only public topics that are not opted out, plus a limited consent view.

## Quick start

```bash
python -m pip install -e .[dev]
```

Create a profile:

```bash
python -m open_recommender.cli create profile.orf --display-name "Alice Example" --device-id laptop
```

Add a topic and inspect the public projection:

```bash
python -m open_recommender.cli topic-set profile.orf orf:technology/python 0.9
python -m open_recommender.cli export-public profile.orf
```

**Inspect your profile** — your `.orf` file is human-readable JSON:

```bash
cat profile.orf | jq .
```

You can see exactly what's stored: your identity, topics, consent flags, and the cryptographic log of all changes. See [Transparency & Security](docs/transparency-and-security.md) for details on what Open Recommender stores and what it doesn't.

Create an encrypted recovery backup:

```bash
python -m open_recommender.cli backup-create profile.orf profile-backup.orfb \
  --backup-passphrase "choose-a-strong-passphrase"
```

Restore from a backup:

```bash
python -m open_recommender.cli backup-restore profile-backup.orfb restored-profile.orf \
  --backup-passphrase "choose-a-strong-passphrase"
```

Run the hosted API locally:

```bash
OPEN_RECOMMENDER_ADMIN_TOKEN=dev-admin-token \
python -m uvicorn open_recommender.service:create_app --factory --reload
```

Push or pull against that API:

```bash
python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000
python -m open_recommender.cli sync-pull profile.orf http://127.0.0.1:8000
```

**Hosted sync (paid tier):** set `OPEN_RECOMMENDER_SYNC_TOKEN` on the server and pass
`--sync-token <token>` to the CLI or `PartnerClient(sync_token=...)` in code. The health
endpoint reports `sync_auth_required: true` when the gate is active.

Run the narrated end-to-end pilot dry-run against a live service:

```bash
python examples/pilot_dry_run.py http://127.0.0.1:8000
```

Run the adopter-facing reference site example:

```bash
python examples/pilot_flow.py http://127.0.0.1:8000 profile.orf --auto-approve
```

Use the thin partner SDK wrapper in site code:

```python
from open_recommender.partner_sdk import PartnerClient

sdk = PartnerClient("http://127.0.0.1:8000")
created = sdk.create_access_request(
    profile_id="<profile_id>",
    site_id="open-news-demo",
    purpose="Personalize the pilot site feed.",
    requested_scopes=["profile.read", "topics.public"],
)
```

Run the sample adopter site:

```bash
SAMPLE_SITE_DEMO_SIGNER_KEY_PATH=profile.orf.key \
python -m uvicorn examples.sample_site:app --reload --port 9001
```

Inspect or act on a Phase 2 site access request:

```bash
python -m open_recommender.cli site-access-request-get <request_id> http://127.0.0.1:8000
python -m open_recommender.cli site-access-request-approve <request_id> http://127.0.0.1:8000 --scope profile.read --scope topics.public
python -m open_recommender.cli site-access-request-deny <request_id> http://127.0.0.1:8000 --reason "User declined this pilot request."
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## API surface

Current FastAPI routes:

- `GET /health`
- `POST /profiles`
- `GET /profiles/{profile_id}/public`
- `GET /profiles/{profile_id}/events?after_clock=...`
- `POST /profiles/{profile_id}/events`
- `POST /profiles/{profile_id}/challenges`
- `POST /profiles/{profile_id}/challenge-response`
- `POST /profiles/{profile_id}/site-access-requests`
- `GET /site-access-requests/{request_id}`
- `POST /site-access-requests/{request_id}/approve`
- `POST /site-access-requests/{request_id}/deny`
- `POST /site-access-requests/{request_id}/exchange`
- `POST /site-access-requests/{request_id}/verify`
- `GET /grant-sessions/{session_id}/projection`
- `GET /consent`
- `GET /consent/grants`
- `GET /consent/site-access-requests/{request_id}`
- `POST /consent/grants/{grant_id}/revoke`
- `POST /consent/site-access-requests/{request_id}/approve`
- `POST /consent/site-access-requests/{request_id}/deny`
- `GET /lens`
- `GET /lens/profiles`
- `GET /lens/profiles/{profile_id}`
- `GET /lens/profiles/{profile_id}/pending-requests`
- `GET /demo/site/{profile_id}`
- `POST /demo/site/{profile_id}/challenge`
- `POST /demo/site/{profile_id}/verify`
- `GET /admin/pilot-sites`
- `GET /admin/audit-events`

FastAPI also serves generated docs at `/docs` and `/redoc` when the app is running.

## Service configuration and guardrails

The reference service reads a small set of environment variables:

- `OPEN_RECOMMENDER_DB_PATH` - override the SQLite database path
- `OPEN_RECOMMENDER_ADMIN_TOKEN` - enable the read-only admin endpoints when set
- `OPEN_RECOMMENDER_RATE_LIMIT_WINDOW_SECONDS` - fixed-window rate-limit window for auth-sensitive routes
- `OPEN_RECOMMENDER_RATE_LIMIT_MAX_REQUESTS` - max requests allowed per client within that window
- `OPEN_RECOMMENDER_PILOT_SITES_PATH` - optional JSON file path for custom pilot-site registration

Use a custom pilot-site file:

```bash
OPEN_RECOMMENDER_PILOT_SITES_PATH=examples/pilot-sites.json \
python -m uvicorn open_recommender.service:create_app --factory --reload
```

Current guardrails:

- auth-sensitive challenge, verify, exchange, approval, denial, and projection routes are rate-limited per client
- admin inspection routes are disabled unless an admin token is configured
- audit events are recorded for access requests, approvals, denials, challenge issuance and verification, grant sessions, and projection reads

## Demo flow

The current demo endpoints show the core product story without a browser app:

1. `GET /demo/site/{profile_id}` returns immediate personalization from the profile's public topics.
2. `POST /demo/site/{profile_id}/challenge` issues a challenge the portable profile owner can sign.
3. `POST /demo/site/{profile_id}/verify` verifies that signature and returns a verified portable-profile session without requiring a site-specific account.

## Browser trust app

The browser trust app is the current localhost-only user surface for understanding one portable profile and reviewing site access.

Open it in a browser:

```text
http://127.0.0.1:8000/lens
http://127.0.0.1:8000/consent
```

Current v0 behavior:

- load a local `.orf` file directly in the browser
- or load a profile already registered in the local service
- inspect what stays on this device
- inspect what is already public
- simulate what a site could see if you approved specific scopes
- review pending site requests from a consent inbox
- jump from a loaded profile into any pending request tied to that profile
- inspect active, revoked, and expired grants
- revoke an active grant to block future exchange attempts for that grant

## Phase 2 CLI approval flow

For the Phase 2 pilot flow, a site first creates a scoped access request through the service API. The ORF user can then inspect and resolve that request from the CLI:

```bash
python -m open_recommender.cli site-access-request-get <request_id> http://127.0.0.1:8000
python -m open_recommender.cli site-access-request-approve <request_id> http://127.0.0.1:8000 --scope profile.read --scope topics.public --scope topics.selective:orf:media/podcasts
```

Or deny it explicitly:

```bash
python -m open_recommender.cli site-access-request-deny <request_id> http://127.0.0.1:8000 --reason "User declined this pilot request."
```

The service also returns a localhost-only browser review link for the same request:

```text
/consent/site-access-requests/<request_id>
```

That page is meant for local review while the service is bound to localhost. It shows the site, purpose, requested scopes, and a plain-language preview of what the site could see if approved.

For pilot adopters, the repo also now includes `examples/pilot_flow.py`, a runnable reference integration that shows the site-side HTTP calls plus the user-side signature handoff in one file.

To validate the flow in a more realistic site-shaped artifact, the repo also includes `examples/sample_site.py`, a tiny adopter app that creates requests against the ORF service and, in localhost demo mode only, can finish the proof step with a clearly-labeled demo signer key path.

After approval, the site begins the exchange:

```bash
curl -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/exchange
```

That response includes a `challenge_payload`. Sign that payload with the ORF private key, then verify it:

```bash
curl -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/verify \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_id": "<challenge_id>",
    "signature": "<ed25519-signature>"
  }'
```

Once verification succeeds, the CLI can inspect the consented projection tied to the verified grant session:

```bash
python -m open_recommender.cli grant-session-projection <session_id> http://127.0.0.1:8000
```

Expected v0 behavior:

- denied or expired requests cannot move into exchange
- expired or replayed challenges are rejected
- the consented projection includes only approved scopes: public topics plus explicitly approved selective topics
- private topics stay out of the projection
- auth-sensitive routes are rate-limited per client

## Admin inspection surfaces

When `OPEN_RECOMMENDER_ADMIN_TOKEN` is set, the reference service exposes read-only operational inspection routes:

```bash
curl -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  http://127.0.0.1:8000/admin/pilot-sites

curl -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  "http://127.0.0.1:8000/admin/audit-events?limit=20"
```

These endpoints are meant for local and pilot investigation, not end-user access.

## Early market fit

The current product fit is narrow on purpose. This repo is best suited today for:

- privacy-conscious early adopters who want to inspect and carry a portable interest profile
- small pilot sites that want a cold-start personalization demo without building a full account system first
- developer-led partners evaluating a user-controlled alternative to closed recommendation onboarding

It is **not** yet the right fit for mainstream consumer login replacement, large-scale ad-tech integrations, or polished non-technical onboarding.

## Repository layout

```text
src/open_recommender/   Core package, CLI, models, API, and SQLite store
tests/                  unittest coverage for models and service flows
docs/                   Deeper contributor and architecture documentation
.github/agents/         Project-local agent definitions
.github/skills/         Project-local skill source files
.agents/skills/         Runtime-compatible mirror for skill discovery
.github/instructions/   Extra contributor/agent instructions
```

## Read more

- [Getting started](docs/getting-started.md)
- [Local proof-of-concept testing](docs/local-poc-testing.md)
- [Pilot integration flow](docs/pilot-integration.md)
- [Architecture](docs/architecture.md)
- [Phase 2 productization brief](docs/phase-2-productization.md)
- [Product roadmap](docs/product-roadmap.md)
- [Contributing](CONTRIBUTING.md)
