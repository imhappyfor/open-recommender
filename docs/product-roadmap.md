# Product roadmap

This document tracks the next meaningful product goals for the current Open Recommender repository.

## Current repo position

Open Recommender is no longer just a protocol sketch. The repo already has:

- portable ORF profiles
- signed local mutations and hosted verification
- public and consented projection behavior
- a locally runnable FastAPI service
- a CLI reference flow for pilot-site access requests
- a localhost browser trust app with a profile lens and consent inbox

That means the project is now **pre-pilot, trust-demo stage**: the core privacy and portability story exists, but the user-facing experience is still too low-level for normal people and too implicit for third-party adopters.

## Early market fit

The most credible near-term fit is:

- small pilot sites that want to demonstrate cold-start personalization without forcing account creation first
- privacy-conscious early adopters who care about seeing and controlling what a site can learn
- developer-led product teams willing to test a local-first, trust-heavy flow before demanding consumer polish

The current repo is **not** yet the right fit for:

- mainstream consumer login replacement
- non-technical users who expect recovery, passkeys, and polished onboarding
- large platforms that need production auth, compliance, and ad-tech integration on day one

## Product thesis

The next move is **not** a broad sync platform or a fully featured hosted app.

The current move is a **trust surface**: a thin UI that makes one thing obvious:

> what exists only on this device, what is public, and what a specific third party would actually see after consent

That trust surface now exists inside the existing hosted service as a localhost-only browser app with a consent inbox and local profile lens.

## Tracked goals

| Status | Goal | Why it matters now | Exit signal |
| --- | --- | --- | --- |
| **Done** | **Consent Review UI v0** | Replaces the current CLI-only approval flow with a human-readable review screen for requested scopes, purpose, and resulting projection. | A pending site access request can be reviewed and approved or denied from a localhost-served browser page. |
| **Done** | **Local Profile Lens v0** | Makes privacy boundaries legible even before a live site request exists. | A user can open a local `.orf` file or locally stored profile and inspect local, public, and site-scoped views. |
| **Done** | **Browser Trust App v0** | Turns the lens and consent review into one coherent localhost trust surface with a consent inbox and guided handoff. | A user can move from profile inspection to pending request review without leaving the browser trust surface. |
| **Done** | **Reference Site Integration Kit v0** | Gives pilot adopters a stable way to test the flow without reverse-engineering the repo. | A runnable reference example plus docs can request scopes, complete proof-of-control, and read a consented projection. |
| **Done** | **Sample Adopter Site v0** | Validates the kit in a site-shaped artifact instead of a script-only demo. | A tiny sample site can create a request, redirect into the trust app, and render a consented projection. |
| **Done** | **Revocation and Audit Story v0** | Makes privacy claims survive scrutiny once third parties start using real projections. | Users can inspect prior grants in the trust app, revoke active grants, and see revocation reflected in audit events. |
| **Done** | **Partner SDK v0 (thin wrapper)** | Reduces integration friction without hiding protocol boundaries or key ownership responsibilities. | A site can use a stable Python wrapper for request/exchange/verify/projection while signer logic remains outside the SDK. |
| **Done** | **Recovery and Device UX v0** | Portable identity becomes fragile without a believable backup and restore path. | The CLI supports encrypted backup/restore with key-match validation and overwrite safeguards, with docs for pilot usage. |
| **Done** | **Hosted Sync Packaging v0** | The first monetisation wedge: sync push/pull as optional paid convenience, not a portability lock-in. | Events endpoints require `Authorization: Bearer <token>` when `OPEN_RECOMMENDER_SYNC_TOKEN` is set; health reports the tier; partner SDK supports the token; pilot dry-run script validates both open and token-gated modes. |

## Recommended current milestone

The pilot stack is now demo-ready. Run `examples/pilot_dry_run.py` against a real service to validate with a pilot partner.

Next natural expansion after a real pilot conversation:

1. **Multi-device sync polish** — let users sync a profile across two devices using backup restore + hosted pull, and document that flow end-to-end.
2. **Pricing surface** — add a lightweight subscription or API-key issuance flow so the hosted sync token can be tied to a customer record.
3. **Consumer onboarding** — passkey or mobile-native identity anchoring once the basic hosted offering has real paying pilot partners.

### Scope

Keep the milestone intentionally narrow:

1. keep the sample site narrow and obviously demo-only where it bends reality
2. make the user-side signing handoff explicit instead of teaching the site to own the key
3. document localhost-only review and demo-signer boundaries before partners cargo-cult them
4. keep the SDK thin and avoid heavyweight abstractions until at least one real pilot validates the flow

### Anti-goals

Do not expand this milestone into:

- a hosted account system
- browser extension work
- OAuth or OIDC wrappers
- multi-device sync UX
- advanced recommendation ranking
- a social or marketplace layer for third-party apps
- consumer-grade login replacement messaging

## Why this wins now

This milestone fits the repo's real maturity:

- **Startup-operator view**: it is the smallest new thing that improves the demo, trust story, and pilot readiness at the same time.
- **UX view**: users cannot meaningfully consent to sharing if they cannot see what a site would get.
- **Privacy view**: a "How sites see me" surface is stronger than adding more sharing power before explanation and review exist.
- **Protocol view**: it reuses existing ORF, scope, and projection behavior instead of inventing a second product shape.

## Product language to keep

Prefer these user-facing phrases in the GUI and docs:

- **Your profile**
- **What stays on this device**
- **What is public**
- **What this site can see**
- **Why this is shared**

Avoid leading with abstract language like "meta personality" in the product UI. It is evocative, but it is less precise than showing a concrete audience-based view of the profile.

## Suggested build order

1. add a reusable scope-preview path that renders what a site would see from a pending request
2. create a localhost-served browser review page around that preview flow
3. polish the wording and visibility explanations until the privacy boundaries are obvious
4. extend the same preview surface into the broader local profile lens
5. publish a minimal reference-site integration example after the review story is credible

## Open questions to resolve when implementation starts

- desktop app shell versus localhost-served web app
- whether site-preview scopes are entered manually or loaded from real access requests first
- how to present selective topics without making the user learn protocol jargon
- what local backup warning or key-path guidance the first GUI must show
