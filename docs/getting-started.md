# Getting started

This guide walks through the current local workflow for Open Recommender: install the package, create an ORF profile, edit it through signed events, run the hosted API, and sync with it.

If you want a copy-pasteable proof-of-concept walkthrough with representative command output, see [Local proof-of-concept testing](local-poc-testing.md).

## Prerequisites

- Python 3.10+
- a shell with `python` available

## Install

From the repository root:

```bash
python -m pip install -e .[dev]
```

## 1. Create a local ORF profile

```bash
python -m open_recommender.cli create profile.orf --display-name "Alice Example" --device-id laptop
```

This writes:

- `profile.orf` — the portable JSON profile document
- `profile.orf.key` — the matching Ed25519 private key in PEM format

The CLI prints JSON including the generated `profile_id`.

## 2. Add or remove preference state

Add a public topic:

```bash
python -m open_recommender.cli topic-set profile.orf orf:technology/python 0.9
```

Add a private topic:

```bash
python -m open_recommender.cli topic-set profile.orf orf:health/sleep 0.4 --visibility private
```

Remove a topic:

```bash
python -m open_recommender.cli topic-remove profile.orf orf:health/sleep
```

Update consent:

```bash
python -m open_recommender.cli consent-set profile.orf hosted_sync false
python -m open_recommender.cli consent-set profile.orf share_public_topics true
```

Opt out of a topic from public sharing:

```bash
python -m open_recommender.cli opt-out-set profile.orf orf:politics/news true
```

Each of these commands loads the private key, creates a signed event, applies it locally, and saves the updated profile document.

## 2a. Create an encrypted backup for recovery

Create a portable backup bundle that includes the profile document plus an encrypted key:

```bash
python -m open_recommender.cli backup-create profile.orf profile-backup.orfb \
  --backup-passphrase "choose-a-strong-passphrase"
```

Restore later (or on another device):

```bash
python -m open_recommender.cli backup-restore profile-backup.orfb restored-profile.orf \
  --backup-passphrase "choose-a-strong-passphrase"
```

Current backup guardrails:

- backup creation verifies that the selected key matches the profile public key
- backup restore verifies key/profile match before writing files
- restore refuses to overwrite existing files unless `--overwrite` is provided

## 3. Inspect the public projection

```bash
python -m open_recommender.cli export-public profile.orf
```

The current public projection includes:

- `profile_id`
- `display_name`
- public topics only
- opted-out topic names
- a reduced consent view with `share_public_topics` and `ad_personalization`
- `updated_at`

Private and selective topics remain in the full profile document but are not included in the public projection.

## 4. Run the hosted API

Start the FastAPI app:

```bash
OPEN_RECOMMENDER_ADMIN_TOKEN=dev-admin-token \
python -m uvicorn open_recommender.service:create_app --factory --reload
```

Useful endpoints:

- `GET /health`
- `POST /profiles`
- `GET /profiles/{profile_id}/public`
- `GET /profiles/{profile_id}/events?after_clock=0`
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

Generated API docs are also available from the running app at `/docs`.

The service also supports:

- `OPEN_RECOMMENDER_DB_PATH` to override the SQLite path
- `OPEN_RECOMMENDER_ADMIN_TOKEN` to enable read-only admin inspection routes
- `OPEN_RECOMMENDER_RATE_LIMIT_WINDOW_SECONDS` and `OPEN_RECOMMENDER_RATE_LIMIT_MAX_REQUESTS` to tune auth-route rate limiting
- `OPEN_RECOMMENDER_PILOT_SITES_PATH` to load pilot-site registrations from a JSON file

Example:

```bash
OPEN_RECOMMENDER_PILOT_SITES_PATH=examples/pilot-sites.json \
python -m uvicorn open_recommender.service:create_app --factory --reload
```

## 4a. Open the browser trust app

The current browser trust app has two localhost-only entry points:

```text
http://127.0.0.1:8000/lens
http://127.0.0.1:8000/consent
```

Use `/lens` to understand one profile before a live site request exists:

```text
http://127.0.0.1:8000/lens
```

From there you can:

- open a local `.orf` file directly in the browser
- or load a profile already registered in the local service
- inspect what stays on this device
- inspect what is already public
- simulate what a site would see under selected scopes

Use `/consent` to review pending live site requests that need a decision:

- see every pending request in one consent inbox
- jump from a request back to the matching profile lens
- approve or deny from the browser with grouped scopes that separate already-public data from selective site-only sharing

Use `/consent/grants` to inspect existing grants:

- review active, revoked, and expired grant status
- revoke active grants from the browser trust app
- block future session exchange attempts for revoked grants

## 4b. Try the demo flow

The demo endpoints show the current "arrive with a portable profile and get personalized immediately" story.

Preview a site session using only the public profile:

```bash
curl http://127.0.0.1:8000/demo/site/<profile_id>
```

Start a proof-of-control challenge:

```bash
curl -X POST http://127.0.0.1:8000/demo/site/<profile_id>/challenge
```

The response includes:

- a public-profile-based personalization preview
- `challenge`
- `challenge_payload`

Today, verification is still a low-level API step: sign `challenge_payload` with the ORF private key and post the signature to `/demo/site/<profile_id>/verify`.

## 4c. Resolve a pilot site access request from the CLI or browser

Phase 2 v0 uses a CLI reference flow because there is no browser app yet.

First, the pilot site creates an access request against the hosted service:

```bash
curl -X POST http://127.0.0.1:8000/profiles/<profile_id>/site-access-requests \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "open-news-demo",
    "purpose": "Personalize the pilot site feed.",
    "requested_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts"
    ]
  }'
```

Or in Python site code:

```python
from open_recommender.partner_sdk import PartnerClient

sdk = PartnerClient("http://127.0.0.1:8000")
created = sdk.create_access_request(
    profile_id="<profile_id>",
    site_id="open-news-demo",
    purpose="Personalize the pilot site feed.",
    requested_scopes=["profile.read", "topics.public"],
)
print(created["access_request"]["request_id"])
```

Then the ORF user can inspect the request and approve or deny it:

```bash
python -m open_recommender.cli site-access-request-get <request_id> http://127.0.0.1:8000
python -m open_recommender.cli site-access-request-approve <request_id> http://127.0.0.1:8000 \
  --scope profile.read \
  --scope topics.public \
  --scope topics.selective:orf:media/podcasts
python -m open_recommender.cli site-access-request-deny <request_id> http://127.0.0.1:8000 \
  --reason "User declined this pilot request."
```

These commands print the service response as JSON so the approved scopes, denial reason, and request status stay explicit.

For a more human-readable review, open the `consent_review_url` returned by the request API in a local browser, or just visit `/consent`. The localhost-only trust app lets the user review pending requests, see a plain-language preview, and approve or deny without the CLI.

If the request is approved, the site starts the grant exchange:

```bash
curl -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/exchange
```

The response includes a `challenge` plus `challenge_payload`. Sign `challenge_payload` with the profile's private key, then verify it:

```bash
curl -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/verify \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_id": "<challenge_id>",
    "signature": "<ed25519-signature>"
  }'
```

Once the service returns a verified `session_id`, you can inspect the consented projection directly:

```bash
python -m open_recommender.cli grant-session-projection <session_id> http://127.0.0.1:8000
```

Current validation rules for this flow:

- denied or expired requests cannot be exchanged
- expired or replayed challenges are rejected during verify
- the projection only includes data covered by the approved scopes
- public topics still honor `share_public_topics`, approved selective topics are included explicitly, and private topics remain excluded
- auth-sensitive routes are rate-limited per client

For a tighter adopter-facing walkthrough, see [Pilot integration flow](pilot-integration.md).

If you want a runnable adopter example instead of piecing the flow together from curl commands, run:

```bash
python examples/pilot_flow.py http://127.0.0.1:8000 profile.orf --auto-approve
```

That example demonstrates the site-side request/exchange/verify calls and the local ORF signer step in one file.

For a site-shaped validation, you can also run the tiny sample adopter app:

```bash
SAMPLE_SITE_DEMO_SIGNER_KEY_PATH=profile.orf.key \
python -m uvicorn examples.sample_site:app --reload --port 9001
```

Then open `http://127.0.0.1:9001` and start a sample session with a known `profile_id`.

## 5. Sync a profile with the hosted API

Push the full profile and its current event log:

```bash
python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000
```

Pull events after the local clock:

```bash
python -m open_recommender.cli sync-pull profile.orf http://127.0.0.1:8000
```

Current sync behavior is simple:

- `sync-push` posts the full local profile first, then posts the local event log
- `sync-pull` fetches events after the local clock window and ignores already-known event IDs

### Hosted sync token (paid tier)

When a service is configured with `OPEN_RECOMMENDER_SYNC_TOKEN`, the sync
push and pull endpoints require a `Bearer` token:

```bash
# Start the service with a sync token (paid tier mode)
OPEN_RECOMMENDER_SYNC_TOKEN=my-secret uvicorn open_recommender.service:create_app --factory

# Push with the token
python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000 --sync-token my-secret

# Pull with the token
python -m open_recommender.cli sync-pull profile.orf http://127.0.0.1:8000 --sync-token my-secret
```

Without the token, push and pull return `401 Unauthorized`. The health endpoint
reports `sync_auth_required: true` when the gate is active.

## 5a. Run the pilot dry-run script

`examples/pilot_dry_run.py` is a narrated end-to-end integration script that
exercises all major flows — profile creation, access request, challenge/verify,
projection, delta sync push/pull — against a real running service:

```bash
# Start the service
uvicorn open_recommender.service:create_app --factory --reload

# Run the dry-run (open tier)
python examples/pilot_dry_run.py http://127.0.0.1:8000

# Run the dry-run against a token-gated service
OPEN_RECOMMENDER_SYNC_TOKEN=my-secret uvicorn open_recommender.service:create_app --factory &
python examples/pilot_dry_run.py http://127.0.0.1:8000 --sync-token my-secret
```

The script exits `0` on success and prints a summary of what was exercised.
Use it as a demo script with a pilot partner or as a smoke test before deployment.

## 6. Run tests

```bash
python -m unittest discover -s tests -v
```

Current tests cover:

- profile merge and conflict rules
- signature verification
- public projection privacy behavior
- hosted API registration, event ingest, event listing, and challenge-response verification
- the demo flow for immediate personalization before and after proof-of-control
- admin audit inspection and auth-route rate limiting
- hosted sync token gate (open tier vs paid tier, 401 on missing/invalid token)
- backup create and restore round-trip, key-mismatch rejection
- partner SDK happy path and error surfacing

## Notes on current scope

Document only what exists today:

- there is no browser app in this repository
- there are no passkey flows
- challenge verification currently uses Ed25519 signatures over a server-issued challenge payload
