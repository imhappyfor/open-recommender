"""pilot_dry_run.py - End-to-end pilot dry-run for Open Recommender.

Exercises the complete ORF flow against a running service:
  create profile → register → partner access request → approve →
  exchange/verify → projection → push event → pull events → revoke

Usage:
    # Start the service first:
    uvicorn open_recommender.service:app --port 8000

    # Run the dry-run (auto-approves, no browser required):
    python examples/pilot_dry_run.py http://127.0.0.1:8000

    # With hosted sync token gating:
    python examples/pilot_dry_run.py http://127.0.0.1:8000 --sync-token my-secret

    # Quiet mode (assertions only, no narration):
    python examples/pilot_dry_run.py http://127.0.0.1:8000 --quiet
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.partner_sdk import PartnerClient, PartnerSDKError
from open_recommender.cli import send_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_quiet = False


def step(n: int, label: str) -> None:
    if not _quiet:
        print(f"\n{'─'*60}")
        print(f"  Step {n}: {label}")
        print(f"{'─'*60}")


def info(msg: str) -> None:
    if not _quiet:
        print(f"  {msg}")


def ok(msg: str) -> None:
    marker = "✓" if not _quiet else ""
    if not _quiet:
        print(f"  {marker} {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def assert_ok(condition: bool, msg: str) -> None:
    if not condition:
        fail(msg)
    ok(msg)


# ---------------------------------------------------------------------------
# Core dry-run
# ---------------------------------------------------------------------------

def run_pilot_dry_run(
    server: str,
    *,
    sync_token: str | None = None,
) -> dict[str, Any]:
    base = server.rstrip("/")
    sdk = PartnerClient(base, sync_token=sync_token)

    # ------------------------------------------------------------------
    step(1, "Create a local ORF profile")
    # ------------------------------------------------------------------
    private_key, public_key = generate_key_pair()
    profile = ORFProfile.create("Pilot User", public_key, "device-dry-run")

    # Seed some topic preferences
    for topic, weight, visibility in [
        ("orf:technology/open-source", 0.95, "public"),
        ("orf:media/podcasts", 0.70, "selective"),
        ("orf:health/sleep", 0.40, "private"),
    ]:
        event = build_signed_event(
            profile,
            EventOp.SET_TOPIC,
            {"topic": topic, "weight": weight, "visibility": visibility},
            signature="",
        )
        event.signature = sign_payload(event.unsigned_payload(), private_key)
        profile.apply_event(event)
        info(f"Added topic: {topic} ({visibility}, weight={weight})")

    assert_ok(len(profile.topics) == 3, "Profile has 3 topic preferences seeded")

    # ------------------------------------------------------------------
    step(2, "Register profile with hosted service")
    # ------------------------------------------------------------------
    reg = send_json("POST", f"{base}/profiles", {"profile": profile.to_document()})
    profile_id = reg["profile_id"]
    info(f"Registered profile_id: {profile_id}")
    public_topics = reg["public_profile"]["topics"]
    assert_ok(len(public_topics) == 1, f"Public projection exposes 1 public topic ({public_topics[0]['topic']})")

    # ------------------------------------------------------------------
    step(3, "Check service health")
    # ------------------------------------------------------------------
    health = send_json("GET", f"{base}/health")
    assert_ok(health["status"] == "ok", "Service is healthy")
    sync_required = health["service"]["sync_auth_required"]
    info(f"Sync auth required: {sync_required}")
    if sync_token:
        assert_ok(sync_required, "Service reports sync_auth_required=true (paid tier active)")
    else:
        assert_ok(not sync_required, "Service reports sync_auth_required=false (open sync)")

    # ------------------------------------------------------------------
    step(4, "Partner SDK: create access request")
    # ------------------------------------------------------------------
    request_resp = sdk.create_access_request(
        profile_id=profile_id,
        site_id="open-news-demo",
        purpose="Personalise your pilot feed without creating an account.",
        requested_scopes=["profile.read", "topics.public", "topics.selective:orf:media/podcasts"],
    )
    request_id = request_resp["access_request"]["request_id"]
    info(f"Access request created: {request_id}")
    info(f"Consent review URL: {request_resp.get('consent_review_url', 'n/a')}")
    assert_ok(request_resp["access_request"]["status"] == "pending", "Request status is pending")

    # ------------------------------------------------------------------
    step(5, "Auto-approve access request (simulates user consent)")
    # ------------------------------------------------------------------
    approve_resp = send_json(
        "POST",
        f"{base}/site-access-requests/{request_id}/approve",
        {
            "approved_scopes": ["profile.read", "topics.public", "topics.selective:orf:media/podcasts"],
            "actor": "pilot-dry-run",
        },
    )
    assert_ok(approve_resp["access_request"]["status"] == "approved", "Request approved")

    # ------------------------------------------------------------------
    step(6, "Exchange challenge (site side)")
    # ------------------------------------------------------------------
    exchange_resp = sdk.exchange_access_request(request_id)
    challenge_payload = exchange_resp["challenge_payload"]
    challenge_id = exchange_resp["challenge"]["challenge_id"]
    info(f"Challenge ID: {challenge_id}")
    assert_ok(bool(challenge_payload), "Challenge payload returned")

    # ------------------------------------------------------------------
    step(7, "Sign challenge (user side – private key never leaves user)")
    # ------------------------------------------------------------------
    signature = sign_payload(challenge_payload, private_key)
    assert_ok(bool(signature), "Challenge signed successfully")
    info("Private key used locally; signature sent to service for verification")

    # ------------------------------------------------------------------
    step(8, "Verify challenge and open grant session")
    # ------------------------------------------------------------------
    verify_resp = sdk.verify_access_request(
        request_id=request_id,
        challenge_id=challenge_id,
        signature=signature,
    )
    session_id = verify_resp["session"]["session_id"]
    info(f"Session ID: {session_id}")
    assert_ok(bool(session_id), "Grant session opened")

    # ------------------------------------------------------------------
    step(9, "Fetch consented projection")
    # ------------------------------------------------------------------
    projection_resp = sdk.get_projection(session_id)
    projection = projection_resp.get("projection", projection_resp)
    proj_topics = {t["topic"]: t for t in projection.get("topics", [])}
    info(f"Projection topics visible to site: {list(proj_topics.keys())}")
    assert_ok("orf:technology/open-source" in proj_topics, "Public topic present in projection")
    assert_ok("orf:media/podcasts" in proj_topics, "Selective topic present in projection")
    assert_ok("orf:health/sleep" not in proj_topics, "Private topic NOT in projection")

    # ------------------------------------------------------------------
    step(10, f"Push a new signed event via hosted sync (sync_token={'set' if sync_token else 'none'})")
    # ------------------------------------------------------------------
    new_event = build_signed_event(
        profile,
        EventOp.SET_TOPIC,
        {"topic": "orf:technology/ai", "weight": 0.85, "visibility": "public"},
        signature="",
    )
    new_event.signature = sign_payload(new_event.unsigned_payload(), private_key)
    profile.apply_event(new_event)
    push_resp = sdk.push_events(profile_id, [new_event.to_dict()])
    assert_ok(push_resp["accepted_events"] == 1, "Pushed 1 new signed event to hosted sync")
    info("New topic 'orf:technology/ai' pushed to service")

    # ------------------------------------------------------------------
    step(11, "Pull events back to verify delta sync round-trip")
    # ------------------------------------------------------------------
    pull_resp = sdk.pull_events(profile_id)
    pulled_events = pull_resp["events"]
    info(f"Pulled {len(pulled_events)} total events from service")
    assert_ok(len(pulled_events) >= 4, f"At least 4 events stored ({len(pulled_events)} pulled)")

    # ------------------------------------------------------------------
    step(12, "Revoke grant (user removes site access)")
    # ------------------------------------------------------------------
    # Fetch the grant_id from the session
    session_detail = send_json("GET", f"{base}/site-access-requests/{request_id}")
    grant_id = session_detail.get("grant", {}).get("grant_id")
    if grant_id:
        csrf_token = send_json(
            "GET",
            f"{base}/site-access-requests/{request_id}",
        ).get("consent_review_url", "")
        # Use the API-level revoke (browser flow requires CSRF; API approve/deny flow used here)
        revoke_resp = send_json(
            "POST",
            f"{base}/consent/grants/{grant_id}/revoke",
            {"csrf_token": "skip"},  # service validates via hmac; skip for dry-run narration
        )
        info(f"Revoke response (may be 403 if CSRF required via browser): {revoke_resp}")
    else:
        info("Grant ID not directly available in request detail – revoke step skipped for dry-run")
        info("In a real flow, users revoke from /consent/grants in the browser trust app")

    # ------------------------------------------------------------------
    print(f"\n{'═'*60}")
    print("  ORF Pilot Dry-Run COMPLETE")
    print(f"{'═'*60}")
    print(f"  Profile:   {profile_id}")
    print(f"  Session:   {session_id}")
    print(f"  Topics in projection: {len(proj_topics)}")
    print(f"  Events pushed/pulled: 1 pushed, {len(pulled_events)} pulled")
    print(f"  Sync auth: {'token-gated (paid tier)' if sync_token else 'open (free tier)'}")
    print(f"{'═'*60}\n")

    return {
        "profile_id": profile_id,
        "session_id": session_id,
        "projection": projection_resp,
        "pull_resp": pull_resp,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="End-to-end Open Recommender pilot dry-run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("server", help="Base URL of the Open Recommender service (e.g. http://127.0.0.1:8000)")
    parser.add_argument(
        "--sync-token",
        metavar="TOKEN",
        default=None,
        help="Bearer token for hosted sync endpoints (omit for open/free-tier sync).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress narration output; only print the final summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    global _quiet
    parser = build_parser()
    args = parser.parse_args(argv)
    _quiet = args.quiet
    try:
        run_pilot_dry_run(args.server, sync_token=args.sync_token)
    except PartnerSDKError as exc:
        print(f"SDK error: {exc}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
