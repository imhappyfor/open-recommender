# Phase 2 productization brief

This repository has finished the foundation milestone. It now has:

- a portable ORF profile model
- signed profile events and verification
- a FastAPI service with hosted sync primitives
- a local CLI for profile creation and editing
- an API-first demo flow
- passing unit coverage for core model and service behavior

That is enough foundation to move from "can this work?" to "what is the first thing an adopter can actually use?"

## Status update

The Phase 2 v0 reference flow described in this brief is now implemented in the repo:

- pilot sites can create scoped access requests
- the CLI can inspect, approve, and deny those requests
- approved requests can complete exchange and verify through challenge signing
- verified grant sessions can fetch a consented projection

The current validation suite covers the intended v0 milestone edges: happy path, denial, expiry, replay protection, and scope filtering.

## Current phase

Open Recommender is currently **foundation-complete with a Phase 2 v0 reference flow, but still pre-pilot**.

Why that assessment fits the repo as it exists today:

- the core data model and signing story are real
- the hosted API is test-covered and locally runnable
- the demo already proves immediate personalization plus proof of profile control
- the model already distinguishes `public`, `selective`, and `private` topics

But it is not yet productized for real adopters because:

- the current user approval surface is still CLI-only
- the current adopter story still needs a cleaner integration guide and example client
- hosted-service hardening is intentionally minimal: basic rate limiting and audit inspection exist, but broader production readiness does not
- there is still no browser consent UX for normal users

## Recommended next milestone

Build the **pilot integration kit v0**.

In plain terms: keep the existing request, approval, exchange, verify, and projection flow, but package it so a first adopter can follow it without reading the whole codebase.

This is the smallest milestone that turns the implemented Phase 2 flow into something a pilot partner can actually evaluate.

## Why this milestone wins now

The biggest gap in the current repo is no longer whether the privacy-aware sharing flow exists. It does. The gap is whether a third-party adopter can understand and exercise it quickly:

> a pilot partner should be able to request scopes, get explicit approval, verify proof of control, and read only the approved projection without bespoke hand-holding.

That is exactly where the current codebase is ready to extend:

- `selective` visibility is already modeled
- challenge-based proof of control already exists
- FastAPI and SQLite persistence are already in place
- CLI-driven flows are already accepted project shape
- read-only admin audit inspection and basic auth-route rate limiting now exist

## Milestone scope

The next pass should ship these pieces together:

1. **Adopter-facing integration guide**
    - exact request, approve, exchange, verify, and projection flow
    - concrete payload shapes and route list
    - clear scope rules and failure modes

2. **Runnable reference example**
    - a thin example client or script for the current pilot flow
    - optimized for local evaluation, not packaging polish

3. **Operational guidance**
    - document the current environment variables and admin token behavior
    - make the basic rate limiting and audit inspection surfaces explicit

4. **Keep scope narrow**
    - no browser consent UX in this pass
    - no new auth model
    - no production-scale ops work yet

## Top risks to resolve next

1. **Adopter integration is still too implicit.**
   The flow exists, but it is still easier to read tests than integrate from docs alone.

2. **CLI approval is still not a normal-user surface.**
   The browser consent app remains a real blocker for anything beyond technical pilots.

3. **Protocol compatibility is still underspecified.**
   `schema_version` exists, but the repo still needs a clear rule for supported major versions, additive fields, and unknown scopes.

4. **Hosted-service hardening is still only pilot-safe, not production-ready.**
   Basic rate limiting and audit inspection help, but secrets handling, retention, backup discipline, and incident tooling are still minimal.

5. **The pilot wedge still needs a believable example.**
   Without a simple reference integration, partners will overestimate the amount of work required.

## Explicit defers

Do not expand Phase 2 into these yet:

- browser UX or passkey flows
- OAuth or OIDC wrappers around ORF flows
- advanced recommender ranking logic
- billing or paid hosted sync packaging
- full production ops hardening beyond the minimum needed for safe local/reference flows
- self-serve site onboarding
- broad taxonomy governance or ecosystem-wide vocabulary design
- multi-server federation

Those are real future needs, but they should come after the project has a clear consented-access integration story.

## Minimal acceptance criteria

We should consider this milestone complete when a reference site can:

1. create a scoped access request
2. let the user approve that request from the ORF side
3. verify proof of profile control
4. receive only the approved projection for that site
5. reuse a short-lived verified session without requiring a site-specific account

## Immediate build order

1. define the request, grant, session, and consented-projection schemas
2. define compatibility rules for schema versions, unknown fields, and scope handling
3. add SQLite migrations plus storage for sites, access requests, grants, sessions, and audit events
4. add FastAPI endpoints for request creation, approval or denial, session exchange, and consented projection reads
5. add CLI commands for reviewing and approving a request
6. add end-to-end tests for happy path, denial, expiry, replay, and scope filtering
7. update README and getting-started docs with the new reference flow

## Concrete repo changes to make next

- `src/open_recommender/models.py`
  - add a consented projection helper that accepts an approved scope set and only returns public plus approved selective topics
  - add schema compatibility helpers used by both the CLI and service

- `src/open_recommender/store.py`
  - add a migration/version table
  - add tables for `sites`, `access_requests`, `grants`, `grant_sessions`, and `audit_events`
  - enforce expiry and one-time-use semantics for challenges and session exchange artifacts

- `src/open_recommender/service.py`
  - add endpoints for request creation, approval or denial, session exchange, and consented projection fetches
  - add basic rate limiting hooks around the new auth-sensitive routes

- `src/open_recommender/cli.py`
  - add commands to inspect, approve, and deny site access requests

- `tests/test_models.py`
  - add consented projection and compatibility tests

- `tests/test_service.py`
  - add end-to-end tests for the v0 grant flow, including denial, expiry, replay, and scope filtering
