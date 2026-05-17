# Transparency & Security

This document describes exactly what Open Recommender stores, logs, and processes — and what it doesn't.

**tl;dr:** Your `.orf` profile file lives on your device. We don't store your recommendation data. We can't see your private topics or the sites you reject. You can read everything we *do* process by opening your file in a text editor.

---

## What We Store

### Hosted Sync (Optional, Paid Tier)

If you enable hosted sync (by setting `OPEN_RECOMMENDER_SYNC_TOKEN`), the service stores:

- **Your signed profile events** — the cryptographic record of your preference updates (topic set/remove, consent changes).
- **Event metadata** — timestamp, logical clock, signature verification status.
- **Your public projections** — the consent-gated view of your data that you explicitly approved.
- **A signed challenge** — a temporary cryptographic proof that you control your Ed25519 key, used only during the challenge-response flow.

### What We Do NOT Store

- **Your private or selective topics** — only the topics you marked `public` are ever stored or processed.
- **Your opted-out topics** — if you remove a topic, that removal is an event (for conflict resolution), but the topic preference itself is not stored.
- **Your browsing history, clicks, or behavioral traces** — we only store preference state, not the path you took to get there.
- **Your Ed25519 private key** — it lives only on your device. We never see it.
- **Any personally identifiable information** beyond what you explicitly put in your profile (display name, device ID).

---

## What Each Component Logs

### Local CLI (`open_recommender.cli`)

**Logs:** Local file system only. No network calls except to sync or partner sites you explicitly request.

**What it does:** Creates, edits, exports, and backs up your `.orf` file. All state changes are written to your local profile file.

**Verification:** Run `cat ~/.orf` and you'll see the exact JSON. No hidden state.

---

### Service (`open_recommender.service`)

**Logs:** HTTP request/response events if `DEBUG` is enabled; otherwise, only error logs.

**What it does:**
1. **Event ingestion** (`POST /profiles/{id}/events`) — accepts signed events, validates signatures, stores append-only events.
2. **Event retrieval** (`GET /profiles/{id}/events`) — returns your signed events (requires valid sync token if token-gating is enabled).
3. **Projection** (`GET /grant-sessions/{session_id}/projection`) — builds a view of your public preferences based on consent settings; does not store this view, only computes it on-demand.
4. **Health check** (`GET /health`) — reports service status; no profile data involved.

**What it doesn't do:**
- Infer hidden preferences from your data
- Track which sites request your profile
- Store requests that failed signature verification
- Log your full profile to stdout or a file
- Analyze patterns across users

---

### Partner SDK (`open_recommender.partner_sdk`)

**Logs:** Only HTTP errors and network timeouts (to help debug integration issues).

**What it does:** Site-side wrapper for calling the service (request access, pull events, verify signatures). No profile data is stored in the SDK; it's stateless.

**What it doesn't do:** Cache or log your preferences locally. Every call to the service uses current state.

---

### Partner Site (Your Application)

**Out of scope for Open Recommender,** but important to understand:
- Your site receives a **projection** (public topics + consent flags).
- What your site *does* with that projection is your responsibility.
- Open Recommender can't see or audit what your site does after receiving the projection.
- If you want to guarantee transparency, publish your own data-handling policy clearly.

---

## How to Verify This

### 1. Inspect Your Profile

Your `.orf` file is human-readable JSON. You can read it directly:

```bash
cat ~/.orf | jq .
```

You'll see:
- `profile_id` — your Ed25519 public key (your identity)
- `display_name` and `device_id` — what you set
- `topics` — the preferences you created, with visibility (`public`/`selective`/`private`)
- `opt_outs` — the topics you removed
- `consent` — flags like `share_public_topics`, `ad_personalization`, `hosted_sync`
- `events` — a signed log of every preference change (creator, timestamp, signature)

Example:
```json
{
  "profile_id": "9a3c...",
  "display_name": "Alice",
  "topics": {
    "orf:technology/python": {
      "score": 0.9,
      "visibility": "public"
    },
    "orf:politics/us": {
      "score": 0.5,
      "visibility": "private"
    }
  },
  "events": [
    {
      "op": "set_topic",
      "topic": "orf:technology/python",
      "score": 0.9,
      "visibility": "public",
      "created_at": "2026-01-15T10:30:00Z",
      "signature": "..."
    }
  ]
}
```

**What this tells you:**
- Nobody can infer your private topics (they're stored locally, not transmitted).
- Every change is signed — if someone modifies your profile, you'll see a signature mismatch.
- Your public topics are the only data the service can process.

### 2. Inspect the Service's View

If you sync with a hosted service, you can request your own events:

```bash
python -m open_recommender.cli sync-pull profile.orf http://open-recommender.example.com --show-raw
```

Compare the service's events to your local `.orf` file. They should be identical (append-only, no deletions, no mutations).

### 3. Read the Code

All source code is on GitHub. The files you should audit:
- `src/open_recommender/models.py` — profile schema and visibility rules
- `src/open_recommender/service.py` — API endpoints and what they process
- `src/open_recommender/store.py` — database schema and query logic
- `tests/test_service.py` — test cases showing what the API accepts and rejects

If something looks off, open an issue or submit a PR.

---

## Consent Revocation & Deletion

### Can I revoke access?

**Yes.** If a partner site has your projection, you can revoke their grant:

```bash
python -m open_recommender.cli grant-revoke profile.orf <site_id>
```

This:
- Removes the site's access grant from your `.orf` file.
- (If hosted sync is enabled) Sends a revocation event to the service.
- Does **not** delete data the site *already received*. The site must respect the revocation on their end.

**Important:** Open Recommender can't force sites to delete data they already have. You need to trust their privacy policy, or choose sites that publish data-retention guarantees.

### Can I delete my data from the service?

**Yes.** There is no "delete account" button because you don't have an account. But if you want to:
1. Stop syncing: delete your local `.orf` file.
2. Contact the service operator: ask them to delete the profile ID `<your-profile-id>` from their database.

Because we don't store PII, deletion is simple — just remove the events log tied to your profile ID.

---

## Rate Limiting & Abuse Prevention

The service enforces rate limiting on:
- **Challenge issuance** — max 10 requests per device per 5 minutes
- **Event ingestion** — max 100 events per profile per 5 minutes

This prevents:
- Brute-force attacks on challenge-response flows
- Flooding the database with junk events
- Profile enumeration (scanning for valid profile IDs)

These limits apply per device, not per IP, so legitimate multi-device users aren't unfairly restricted.

---

## Data Retention

**Hosted sync:**
- Events are kept forever (append-only log).
- Backups are encrypted at rest (the service operator has a key, but cannot decrypt individual events).

**Local CLI:**
- Your `.orf` file is kept as long as you don't delete it.
- Backups are encrypted with your passphrase (nobody but you can decrypt them).

**Service logs:**
- Request/response logs are rotated daily (kept for 7 days by default).
- Error logs are kept for 30 days.
- No profile data is included in logs unless a request fails signature verification (in which case we log the validation error, not the profile itself).

---

## Security Assumptions

Open Recommender assumes:

1. **Your device is not compromised.** If malware has access to your `.orf` file, it has your preferences. There's no solution for a compromised device.
2. **The service operator is honest.** We publish the code, but we can't prevent an operator from running different code. Use a trusted service, or run your own.
3. **Signature verification works.** We use Ed25519, which is cryptographically sound. But if your private key is stolen, an attacker can forge signatures on your behalf.
4. **Partner sites honor consent.** If you approve a topic for a site, we can't prevent them from storing or selling that data. You must read their privacy policy.
5. **Network eavesdropping is prevented by TLS.** All communication to the service should use HTTPS. HTTP is unsafe.

---

## What This Doesn't Guarantee

Open Recommender is **not:**

- A guarantee that you're anonymous (your profile ID is deterministic from your Ed25519 key; observant sites could correlate you across platforms).
- A replacement for a privacy policy (you still need to read and trust each site's data handling).
- A technical solution to the problem of data shared with consent (if you approve a topic, sites can use it; we can't technically prevent that).
- Protection against algorithmic bias (we don't audit whether sites use your preferences fairly).

---

## Questions?

- **Is this code auditable?** Yes. Everything is open-source. File an issue or submit an audit report.
- **Can I run my own service?** Yes. The service code is public. Run it on your own infrastructure.
- **What if I don't trust the hosted sync?** Don't use it. The CLI works entirely offline, and you can manually sync events by sharing your `.orf` file.
- **What if a site violates the consent I gave?** Report it to the site, to Open Recommender, and (if applicable) to a privacy regulator.

We aim to make the system transparent enough that you can trust it by *understanding* it, not by blind faith.
