# Local proof-of-concept testing

This page is the fastest way to show that the repo already demonstrates something real locally:

1. create and edit a portable ORF profile
2. inspect the public projection
3. run the FastAPI service
4. exercise the demo endpoints
5. complete the site-scoped approval flow and inspect the resulting projection
6. run the automated tests

Everything below is grounded in the current repository behavior. IDs, timestamps, and signatures will differ on each run.

## Prerequisites

From the repository root:

```bash
python -m pip install -e '.[dev]'
mkdir -p .poc-work
```

## 1. Create a profile and add local preference state

Create a new portable profile and keep the CLI's JSON output so the profile ID can be reused in later steps:

```bash
python -m open_recommender.cli create .poc-work/profile.orf \
  --display-name "Alice Example" \
  --device-id laptop | tee .poc-work/create.json
```

What it does:

- writes `.poc-work/profile.orf`
- writes `.poc-work/profile.orf.key`
- prints the generated `profile_id`

Representative output:

```json
{
  "profile_path": ".poc-work/profile.orf",
  "key_path": ".poc-work/profile.orf.key",
  "profile_id": "orf:profile:70075877fb450ebfc1acce86f6879e08"
}
```

Capture the generated profile ID in a shell variable:

```bash
PROFILE_ID=$(python - <<'PY'
import json
from pathlib import Path
print(json.loads(Path(".poc-work/create.json").read_text())["profile_id"])
PY
)
```

Add one public topic, one selective topic, and one private topic:

```bash
python -m open_recommender.cli topic-set .poc-work/profile.orf orf:technology/python 0.9
python -m open_recommender.cli topic-set .poc-work/profile.orf orf:media/podcasts 0.7 --visibility selective
python -m open_recommender.cli topic-set .poc-work/profile.orf orf:health/sleep 0.5 --visibility private
```

What they do:

- create signed local events
- update the saved ORF profile document
- return no stdout on success

## 2. Inspect the public projection

Export only the public-facing view:

```bash
python -m open_recommender.cli export-public .poc-work/profile.orf
```

What it does:

- shows the shareable profile surface
- includes only public topics
- keeps selective and private topics out of the public projection

Representative output:

```json
{
  "consent": {
    "ad_personalization": true,
    "share_public_topics": true
  },
  "display_name": "Alice Example",
  "opt_out_topics": [],
  "profile_id": "orf:profile:70075877fb450ebfc1acce86f6879e08",
  "schema_version": "0.1.0",
  "topics": [
    {
      "topic": "orf:technology/python",
      "updated_at": "2026-05-16T20:33:29+00:00",
      "visibility": "public",
      "weight": 0.9
    }
  ],
  "updated_at": "2026-05-16T20:33:29+00:00"
}
```

## 3. Run the API locally

In a separate terminal, start the FastAPI service:

```bash
OPEN_RECOMMENDER_ADMIN_TOKEN=dev-admin-token \
python -m uvicorn open_recommender.service:create_app --factory
```

Check that it is up:

```bash
curl -s http://127.0.0.1:8000/health
```

Representative output:

```json
{
  "status": "ok",
  "service": {
    "admin_endpoints_enabled": true,
    "rate_limit_max_requests": 20,
    "rate_limit_window_seconds": 60
  }
}
```

Push the local profile into the service:

```bash
python -m open_recommender.cli sync-push .poc-work/profile.orf http://127.0.0.1:8000
```

What it does:

- registers the full profile with `POST /profiles`
- posts the local signed event log to `POST /profiles/{profile_id}/events`
- returns no stdout on success

Confirm that the service exposes the same public profile:

```bash
curl -s http://127.0.0.1:8000/profiles/$PROFILE_ID/public
```

Representative output excerpt:

```json
{
  "profile_id": "orf:profile:70075877fb450ebfc1acce86f6879e08",
  "display_name": "Alice Example",
  "topics": [
    {
      "topic": "orf:technology/python",
      "visibility": "public",
      "weight": 0.9
    }
  ]
}
```

## 4. Exercise the demo endpoints

Preview the site's immediate personalization before any proof-of-control step:

```bash
curl -s http://127.0.0.1:8000/demo/site/$PROFILE_ID
```

What it does:

- reads the public profile from the service
- derives a simple personalized preview
- shows that the site does not need a site-specific account to start personalizing

Representative output excerpt:

```json
{
  "demo": {
    "site": "open-news-demo",
    "site_account_required": false,
    "verified": false
  },
  "personalization": {
    "mode": "public-profile",
    "featured_topics": [
      "orf:technology/python"
    ],
    "recommendations": [
      {
        "item_id": "demo-technology-python",
        "stage": "public-preview"
      }
    ]
  },
  "session": {
    "portable_profile_session": false
  }
}
```

Start a demo challenge:

```bash
curl -s -X POST http://127.0.0.1:8000/demo/site/$PROFILE_ID/challenge \
  | tee .poc-work/demo-challenge.json
```

Representative output excerpt:

```json
{
  "challenge": {
    "challenge_id": "GLlC1wSlQlUgWbjc",
    "profile_id": "orf:profile:70075877fb450ebfc1acce86f6879e08"
  },
  "instructions": "Sign challenge_payload with the ORF private key, then POST the signature to /demo/site/{profile_id}/verify."
}
```

Sign the returned `challenge_payload` with the profile's private key:

```bash
python - <<'PY'
import json
from pathlib import Path
from open_recommender.crypto import load_private_key, sign_payload

challenge = json.loads(Path(".poc-work/demo-challenge.json").read_text())
private_key = load_private_key(".poc-work/profile.orf.key")
signature = sign_payload(challenge["challenge_payload"], private_key)
payload = {
    "challenge_id": challenge["challenge"]["challenge_id"],
    "signature": signature,
}
Path(".poc-work/demo-verify.json").write_text(json.dumps(payload), encoding="utf-8")
print("Wrote .poc-work/demo-verify.json")
PY
```

Verify the challenge:

```bash
curl -s -X POST http://127.0.0.1:8000/demo/site/$PROFILE_ID/verify \
  -H "Content-Type: application/json" \
  --data-binary @.poc-work/demo-verify.json
```

Representative output excerpt:

```json
{
  "demo": {
    "verified": true,
    "proof": "challenge-signature"
  },
  "personalization": {
    "mode": "verified-profile"
  },
  "session": {
    "portable_profile_session": true,
    "can_save_state_without_account": true
  }
}
```

## 5. Complete the site-scoped approval flow

Create a site access request:

```bash
curl -s -X POST http://127.0.0.1:8000/profiles/$PROFILE_ID/site-access-requests \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "open-news-demo",
    "purpose": "Personalize the pilot site feed.",
    "requested_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts",
      "topics.selective:orf:health/sleep",
      "unknown.scope"
    ]
  }' | tee .poc-work/site-request-create.json
```

What it does:

- creates a pending site access request
- normalizes supported scopes
- records unsupported scopes separately instead of failing

Representative output excerpt:

```json
{
  "access_request": {
    "request_id": "d22d94d9-1719-461d-b9b1-229d5f6d8a03",
    "site_id": "open-news-demo",
    "status": "pending",
    "requested_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:health/sleep",
      "topics.selective:orf:media/podcasts"
    ],
    "ignored_requested_scopes": [
      "unknown.scope"
    ]
  }
}
```

Capture the request ID:

```bash
REQUEST_ID=$(python - <<'PY'
import json
from pathlib import Path
print(json.loads(Path(".poc-work/site-request-create.json").read_text())["access_request"]["request_id"])
PY
)
```

Inspect the request from the CLI:

```bash
python -m open_recommender.cli site-access-request-get $REQUEST_ID http://127.0.0.1:8000
```

Approve only the scopes the user wants to grant:

```bash
python -m open_recommender.cli site-access-request-approve $REQUEST_ID http://127.0.0.1:8000 \
  --scope profile.read \
  --scope topics.public \
  --scope topics.selective:orf:media/podcasts
```

Representative output excerpt:

```json
{
  "access_request": {
    "status": "approved",
    "approved_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts"
    ]
  },
  "grant": {
    "grant_id": "34ca5e75-6f45-4ca1-a6f0-80969f73537d",
    "exchange_method": "challenge"
  }
}
```

Start the exchange:

```bash
curl -s -X POST http://127.0.0.1:8000/site-access-requests/$REQUEST_ID/exchange \
  | tee .poc-work/site-request-exchange.json
```

Sign the exchange `challenge_payload`:

```bash
python - <<'PY'
import json
from pathlib import Path
from open_recommender.crypto import load_private_key, sign_payload

exchange = json.loads(Path(".poc-work/site-request-exchange.json").read_text())
private_key = load_private_key(".poc-work/profile.orf.key")
signature = sign_payload(exchange["challenge_payload"], private_key)
payload = {
    "challenge_id": exchange["challenge"]["challenge_id"],
    "signature": signature,
}
Path(".poc-work/site-request-verify.json").write_text(json.dumps(payload), encoding="utf-8")
print("Wrote .poc-work/site-request-verify.json")
PY
```

Verify the exchange and get a grant session:

```bash
curl -s -X POST http://127.0.0.1:8000/site-access-requests/$REQUEST_ID/verify \
  -H "Content-Type: application/json" \
  --data-binary @.poc-work/site-request-verify.json \
  | tee .poc-work/site-request-verified.json
```

Representative output excerpt:

```json
{
  "verified": true,
  "session": {
    "session_id": "cc537518-2cdf-442f-baab-de72dc7b89fd",
    "site_id": "open-news-demo",
    "approved_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts"
    ]
  }
}
```

Capture the session ID:

```bash
SESSION_ID=$(python - <<'PY'
import json
from pathlib import Path
print(json.loads(Path(".poc-work/site-request-verified.json").read_text())["session"]["session_id"])
PY
)
```

Inspect the resulting consented projection:

```bash
python -m open_recommender.cli grant-session-projection $SESSION_ID http://127.0.0.1:8000
```

What it proves:

- `topics.public` includes the public Python topic
- `topics.selective:orf:media/podcasts` includes the selective podcasts topic
- the private `orf:health/sleep` topic stays excluded

Representative output excerpt:

```json
{
  "projection": {
    "site_id": "open-news-demo",
    "grant_id": "34ca5e75-6f45-4ca1-a6f0-80969f73537d",
    "granted_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts"
    ],
    "topics": [
      {
        "topic": "orf:media/podcasts",
        "visibility": "selective",
        "weight": 0.7
      },
      {
        "topic": "orf:technology/python",
        "visibility": "public",
        "weight": 0.9
      }
    ]
  }
}
```

## 6. Run the automated tests

```bash
python -m unittest discover -s tests -v
```

What it does:

- runs the current model, CLI, and service coverage
- validates the public-profile, demo, challenge, and site-access flows already implemented in the repo
- covers admin audit inspection and auth-route rate limiting

Representative output excerpt:

```text
...
Ran 20 tests in 0.49s

OK
```

## 7. Inspect pilot-site and audit state

If you started the service with `OPEN_RECOMMENDER_ADMIN_TOKEN`, you can inspect the pilot registry and recent audit trail:

```bash
curl -s -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  http://127.0.0.1:8000/admin/pilot-sites

curl -s -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  "http://127.0.0.1:8000/admin/audit-events?limit=10"
```

What they do:

- show which pilot sites the reference service currently recognizes
- show read-only audit records for access requests, approvals, challenges, sessions, and projection reads

Representative audit output excerpt:

```json
{
  "events": [
    {
      "event_type": "access-request.created",
      "site_id": "open-news-demo",
      "request_id": "d22d94d9-1719-461d-b9b1-229d5f6d8a03"
    }
  ]
}
```

## What this proof of concept demonstrates

If all of the commands above succeed, the repo currently proves that Open Recommender can:

- keep a signed portable profile local-first
- expose a limited public projection
- serve that profile through a FastAPI service
- personalize a demo site immediately from public profile data
- verify profile control through challenge signing
- issue a site-scoped consented projection that includes approved selective topics while keeping private topics out
- expose a minimal operational audit trail for pilot debugging without opening admin data by default
