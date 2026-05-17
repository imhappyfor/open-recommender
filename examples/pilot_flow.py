from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from open_recommender.cli import load_profile, private_key_path_for_profile, send_json
from open_recommender.crypto import load_private_key, sign_payload


def create_access_request(
    server: str,
    *,
    profile_id: str,
    site_id: str,
    purpose: str,
    requested_scopes: list[str],
) -> dict[str, Any]:
    return send_json(
        "POST",
        f"{server.rstrip('/')}/profiles/{profile_id}/site-access-requests",
        {
            "site_id": site_id,
            "purpose": purpose,
            "requested_scopes": requested_scopes,
        },
    )


def approve_access_request(
    server: str,
    *,
    request_id: str,
    approved_scopes: list[str],
) -> dict[str, Any]:
    return send_json(
        "POST",
        f"{server.rstrip('/')}/site-access-requests/{request_id}/approve",
        {
            "approved_scopes": approved_scopes,
            "actor": "reference-site-example",
        },
    )


def exchange_verify_and_fetch_projection(
    server: str,
    *,
    request_id: str,
    private_key_path: Path,
) -> dict[str, Any]:
    exchange_response = send_json(
        "POST",
        f"{server.rstrip('/')}/site-access-requests/{request_id}/exchange",
    )

    # This is the user-side ORF signer step. The site never owns this private key.
    private_key = load_private_key(private_key_path)
    signature = sign_payload(exchange_response["challenge_payload"], private_key)

    verify_response = send_json(
        "POST",
        f"{server.rstrip('/')}/site-access-requests/{request_id}/verify",
        {
            "challenge_id": exchange_response["challenge"]["challenge_id"],
            "signature": signature,
        },
    )
    session_id = verify_response["session"]["session_id"]
    projection_response = send_json(
        "GET",
        f"{server.rstrip('/')}/grant-sessions/{session_id}/projection",
    )
    return {
        "exchange": exchange_response,
        "verify": verify_response,
        "projection": projection_response,
    }


def run_reference_pilot_flow(
    server: str,
    *,
    profile_path: str | Path,
    key_path: str | Path | None = None,
    site_id: str = "open-news-demo",
    purpose: str = "Personalize the pilot site feed.",
    requested_scopes: list[str] | None = None,
    approved_scopes: list[str] | None = None,
    auto_approve: bool = False,
) -> dict[str, Any]:
    requested = requested_scopes or [
        "profile.read",
        "topics.public",
        "topics.selective:orf:media/podcasts",
    ]
    approved = approved_scopes or list(requested)

    profile_path = Path(profile_path)
    profile = load_profile(profile_path)
    resolved_key_path = private_key_path_for_profile(profile_path, str(key_path) if key_path else None)

    request_response = create_access_request(
        server,
        profile_id=profile.profile_id,
        site_id=site_id,
        purpose=purpose,
        requested_scopes=requested,
    )
    request_id = request_response["access_request"]["request_id"]

    approval_response = None
    if auto_approve:
        approval_response = approve_access_request(
            server,
            request_id=request_id,
            approved_scopes=approved,
        )
    else:
        print(
            "Review and approve the request before continuing:\n"
            f"{request_response['consent_review_url']}",
            flush=True,
        )
        input("Press Enter after the request has been approved in the browser trust app or CLI...")

    flow_response = exchange_verify_and_fetch_projection(
        server,
        request_id=request_id,
        private_key_path=resolved_key_path,
    )

    return {
        "request": request_response,
        "approval": approval_response,
        **flow_response,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the reference Open Recommender pilot site flow.")
    parser.add_argument("server", help="Base URL of the Open Recommender service, for example http://127.0.0.1:8000")
    parser.add_argument("profile_path", help="Path to the local ORF profile used for the signer step")
    parser.add_argument("--key-path", help="Optional path to the matching private key PEM file")
    parser.add_argument("--site-id", default="open-news-demo")
    parser.add_argument("--purpose", default="Personalize the pilot site feed.")
    parser.add_argument(
        "--requested-scope",
        action="append",
        dest="requested_scopes",
        help="Requested scope. Repeat the flag to add more scopes.",
    )
    parser.add_argument(
        "--approved-scope",
        action="append",
        dest="approved_scopes",
        help="Approved scope used only with --auto-approve. Defaults to the requested scopes.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Local demo mode: approve the request through the service API instead of waiting for manual browser or CLI approval.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    response = run_reference_pilot_flow(
        args.server,
        profile_path=args.profile_path,
        key_path=args.key_path,
        site_id=args.site_id,
        purpose=args.purpose,
        requested_scopes=args.requested_scopes,
        approved_scopes=args.approved_scopes,
        auto_approve=args.auto_approve,
    )
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
