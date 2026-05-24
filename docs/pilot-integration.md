# Pilot integration flow

This guide documents the current adopter-facing reference flow for a manually registered pilot site.

It is intentionally narrow:

1. a site asks for named scopes
2. the user approves or denies them from the ORF side
3. the site proves control of the portable profile through challenge signing
4. the site receives a short-lived consented projection

The integration kit is narrow by design: one stable flow, one pre-registered pilot `site_id`, and one reference example script.

## Prerequisites

- the FastAPI service is running locally
- the ORF profile has already been created and pushed to the service
- you know the `profile_id`
- the `site_id` has already been registered in the running service

The default local service pre-registers:

- `open-news-demo`

If you want to test with a different `site_id`, load a custom pilot-site file:

```bash
OPEN_RECOMMENDER_PILOT_SITES_PATH=examples/pilot-sites.json \
python -m uvicorn open_recommender.service:create_app --factory --reload
```

The file format is a JSON array of site entries with at least:

- `site_id`
- `site_name`
- `allowed_scopes`

`allow_selective_topics` is optional (defaults to `false`).

There is no self-serve site-registration API.

## Python partner SDK

For site-side integration code, the repo includes `open_recommender.partner_sdk.PartnerClient`.

Supported operations:

- create access request
- inspect request status
- begin exchange
- verify challenge signature
- fetch consented projection

Example:

```python
from open_recommender.partner_sdk import PartnerClient

sdk = PartnerClient("http://127.0.0.1:8000")
created = sdk.create_access_request(
    profile_id=profile_id,
    site_id="open-news-demo",
    purpose="Personalize the pilot site feed.",
    requested_scopes=[
        "profile.read",
        "topics.public",
        "topics.selective:orf:media/podcasts",
    ],
)
request_id = created["access_request"]["request_id"]
```

Important boundary:

- this SDK is site-side only and does not include key handling
- the user-side ORF client still signs `challenge_payload` outside the site process
- unknown fields returned by the service are preserved as-is in response JSON payloads

Current localhost constraint:

- the browser trust app at `/lens` and `/consent` only works when the ORF service itself is running on localhost
- the `consent_review_url` returned by the service is therefore a local-review surface, not a production remote consent URL

Optional but useful for pilot inspection:

```bash
export OPEN_RECOMMENDER_ADMIN_TOKEN=dev-admin-token
```

## 1. Site creates an access request

```bash
curl -s -X POST http://127.0.0.1:8000/profiles/<profile_id>/site-access-requests \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "open-news-demo",
    "purpose": "Personalize the pilot site feed.",
    "requested_scopes": [
      "profile.read",
      "topics.public",
      "topics.selective:orf:media/podcasts",
      "consent.summary"
    ]
  }'
```

Response shape:

- `profile_id`
- `access_request.request_id`
- `consent_review_url`
- normalized `requested_scopes`
- `status: "pending"`
- any ignored unknown scopes under `ignored_requested_scopes`

## 2. User inspects and resolves the request

Inspect from the CLI:

```bash
python -m open_recommender.cli site-access-request-get <request_id> http://127.0.0.1:8000
```

Or open the browser review page locally:

```text
http://127.0.0.1:8000/consent/site-access-requests/<request_id>
```

The current browser review page is intentionally narrow:

- localhost-only
- plain-language scope descriptions
- preview of what the site could see if approved
- approve selected scopes or deny directly from the browser
- available from the consent inbox at `/consent` as well as from the per-request URL

Approve:

```bash
python -m open_recommender.cli site-access-request-approve <request_id> http://127.0.0.1:8000 \
  --scope profile.read \
  --scope topics.public \
  --scope topics.selective:orf:media/podcasts
```

Or deny:

```bash
python -m open_recommender.cli site-access-request-deny <request_id> http://127.0.0.1:8000 \
  --reason "User declined this pilot request."
```

Approval rules in the current repo:

- approved scopes must be a subset of the request
- site-specific allowed scopes are enforced
- denied or expired requests cannot move into exchange
- private topics never become grantable through this flow

## 3. Site begins the grant exchange

```bash
curl -s -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/exchange
```

Response shape:

- `access_request`
- `grant`
- `challenge`
- `challenge_payload`

`challenge_payload` is the canonical object to sign. `challenge` and `challenge_payload` contain the same data; the duplicate field is there for compatibility with the demo flow.

The important handoff is:

1. the **site** calls `/exchange`
2. the **user-side ORF client** signs `challenge_payload` with the ORF private key
3. the **site** posts that signature to `/verify`

The site must never own the user's ORF private key.

Role split summary:

| Actor | Responsibility |
| --- | --- |
| site | request scopes, begin exchange, verify proof, read consented projection |
| user's ORF client | review consent, sign `challenge_payload`, keep the private key local |
| localhost demo signer | optional validation helper used only in sample code on one machine; not a production site pattern |

## 4. Site verifies proof of control

```bash
curl -s -X POST http://127.0.0.1:8000/site-access-requests/<request_id>/verify \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_id": "<challenge_id>",
    "signature": "<ed25519-signature>"
  }'
```

Response shape:

- `verified: true`
- `grant`
- `session`

Important validation rules:

- challenges are one-time use
- challenges expire
- request, site, grant, and challenge type bindings are enforced
- repeated verify attempts with the same challenge fail

## Reference integration example

The repo includes a runnable example that shows both sides of the handoff in one file:

```bash
python examples/pilot_flow.py http://127.0.0.1:8000 profile.orf --auto-approve
```

What it does:

- creates a site access request
- optionally auto-approves it for local demo mode
- begins the exchange as the site
- signs `challenge_payload` with the local ORF private key to simulate the user-side signer step
- verifies proof of control
- fetches the consented projection

For a more realistic run, omit `--auto-approve`, open the printed `consent_review_url`, approve it from the localhost trust app, then press Enter to continue.

## Sample adopter site

The repo also includes a tiny sample adopter app:

```bash
uvicorn examples.sample_site:app --reload --port 9001
```

Optional localhost-only demo signer mode:

```bash
export SAMPLE_SITE_DEMO_SIGNER_KEY_PATH=profile.orf.key
uvicorn examples.sample_site:app --reload --port 9001
```

Open:

```text
http://127.0.0.1:9001
```

What the sample site proves:

- a third-party site can create a scoped request against the ORF service
- the user is redirected into the localhost trust app for approval
- after approval, the site can complete exchange and read the consented projection

Important boundary:

- without `SAMPLE_SITE_DEMO_SIGNER_KEY_PATH`, the sample site stops after approval and expects an external signer flow
- with `SAMPLE_SITE_DEMO_SIGNER_KEY_PATH`, the sample can finish the challenge locally only to validate the protocol on one machine
- that demo signer mode is **not** a valid production pattern and must never be copied into a real third-party site

## 5. Site reads the consented projection

```bash
curl -s http://127.0.0.1:8000/grant-sessions/<session_id>/projection
```

The projection currently includes only data allowed by the approved scopes:

- public topics
- explicitly approved selective topics
- minimal profile metadata
- consent summary only when requested and approved

The projection never includes:

- private topics
- unapproved selective topics
- data outside the session's approved scope set

## 6. Pilot-safe guardrails in the reference service

Current guardrails:

- auth-sensitive routes are rate-limited per client
- admin endpoints are disabled unless `OPEN_RECOMMENDER_ADMIN_TOKEN` is configured
- audit records are stored for access requests, approvals, denials, challenges, grant sessions, and projection reads

Time limits:

| Item | Default |
| --- | --- |
| challenge TTL | 5 minutes |
| grant session TTL | 30 minutes |
| access grant TTL | 7 days |

If the challenge or grant session expires, the site should begin the exchange flow again and obtain a fresh challenge or session.

Useful admin inspection calls:

```bash
curl -s -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  http://127.0.0.1:8000/admin/pilot-sites

curl -s -H "X-Open-Recommender-Admin-Token: dev-admin-token" \
  "http://127.0.0.1:8000/admin/audit-events?limit=20"
```

## 7. What this flow proves

This pilot flow proves that Open Recommender can:

- let a site integrate around a stable scoped access contract
- keep user approval explicit and portable-profile-centered
- avoid site-specific account creation in the reference flow
- limit the shared projection to what the user actually approved
- give the operator a small audit surface for pilot debugging

## 8. Revoke future reuse

From the localhost trust app, open:

```text
http://127.0.0.1:8000/consent/grants
```

Revocation behavior:

- active grants can be revoked from the browser trust app
- revoked grants are recorded in audit events as `grant.revoked`
- revoked grants cannot mint new exchange sessions
- sites must create a new access request and get fresh approval to regain access
