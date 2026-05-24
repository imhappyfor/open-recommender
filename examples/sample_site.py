from __future__ import annotations

import json
import os
import base64
from pathlib import Path
from typing import Any, Callable
from urllib import request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse


def send_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, method=method, headers=headers)
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _encode_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _load_demo_signer(path: Path) -> Ed25519PrivateKey:
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Demo signer must be an Ed25519 private key.")
    return private_key


def _render_page(
    *,
    profile_id: str | None = None,
    request_data: dict[str, Any] | None = None,
    request_status: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    error_message: str | None = None,
    demo_signer_enabled: bool,
) -> str:
    banner = ""
    if demo_signer_enabled:
        banner = (
            "<div class='banner warning'>"
            "<strong>Localhost demo signer enabled.</strong> This sample can finish the proof step with a local key file only because it is a demo running on the same device. A real third-party site must never hold the user's ORF private key."
            "</div>"
        )

    request_card = ""
    if request_data is not None:
        access_request = request_data["access_request"]
        status = request_status["access_request"]["status"] if request_status is not None else access_request["status"]
        action_html = (
            "<p class='muted'>Approve the request from the localhost trust app, then come back here and continue.</p>"
            f"<p><a class='button secondary' href='{request_data['consent_review_url']}'>Open consent review</a></p>"
        )
        if status == "approved":
            if demo_signer_enabled:
                action_html = (
                    "<p class='muted'>The sample site can now finish the challenge flow using the localhost demo signer.</p>"
                    f"<form method='post' action='/session/{access_request['request_id']}/complete'>"
                    "<button type='submit'>Complete sample sign-in</button>"
                    "</form>"
                )
            else:
                action_html = (
                    "<p class='muted'>This request is approved. To finish the proof step for a live test, use the reference integration script or another user-side signer.</p>"
                )

        request_card = f"""
        <section class="card">
          <h2>Current request</h2>
          <p><strong>Request ID:</strong> {access_request['request_id']}</p>
          <p><strong>Status:</strong> {status}</p>
          <p><strong>Purpose:</strong> {access_request['purpose']}</p>
          <p><strong>Required scopes:</strong> {", ".join(access_request.get('required_scopes', [])) or "None"}</p>
          <p><strong>Optional scopes:</strong> {", ".join(access_request.get('optional_scopes', [])) or "None"}</p>
          {action_html}
        </section>
        """

    projection_card = ""
    if projection is not None:
        topic_items = "".join(
            f"<li>{topic['topic']} ({topic['visibility']})</li>"
            for topic in projection["projection"].get("topics", [])
        )
        projection_card = f"""
        <section class="card">
          <h2>Personalized sample feed</h2>
          <p><strong>Display name:</strong> {projection['projection'].get('display_name')}</p>
          <p class="muted">These topics came from the consented projection returned by the ORF service.</p>
          <ul>{topic_items or "<li>No topics returned.</li>"}</ul>
        </section>
        """

    error_html = ""
    if error_message is not None:
        error_html = f"<div class='banner error'><strong>Sample site error:</strong> {error_message}</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open News Demo sample site</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #020617; color: #e2e8f0; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 24px 20px 48px; }}
    .lead, .muted {{ color: #94a3b8; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
    .banner {{ margin-bottom: 16px; padding: 14px 16px; border-radius: 12px; }}
    .warning {{ background: #451a03; border: 1px solid #b45309; }}
    .error {{ background: #450a0a; border: 1px solid #dc2626; }}
    .button, button {{ display: inline-block; border: 0; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; text-decoration: none; cursor: pointer; font-size: 14px; }}
    .button.secondary {{ background: #334155; }}
    input[type='text'] {{ width: 100%; margin-top: 8px; padding: 10px 12px; border-radius: 10px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; }}
  </style>
</head>
<body>
  <main>
    <h1>Open News Demo sample site</h1>
    <p class="lead">This sample behaves like a pilot adopter site. It creates a scoped request against the ORF service, sends the user to the localhost trust app for approval, then reads a consented projection.</p>
    {banner}
    {error_html}
    <div class="grid">
      <section class="card">
        <h2>Start a sample session</h2>
        <p class="muted">Enter a portable profile ID that already exists in the ORF service.</p>
        <form method="get" action="/connect">
          <label>
            <strong>Profile ID</strong>
            <input type="text" name="profile_id" value="{profile_id or ''}" placeholder="orf:profile:...">
          </label>
          <p style="margin-top: 16px;"><button type="submit">Request personalized access</button></p>
        </form>
      </section>
      <section class="card">
        <h2>Role split</h2>
        <ul class="muted">
          <li><strong>This site</strong> requests scopes, starts exchange, and reads the consented projection.</li>
          <li><strong>The user's ORF client</strong> approves the request and signs the challenge.</li>
          <li><strong>The demo signer</strong> exists only for localhost validation and must not be copied into a real deployment.</li>
        </ul>
        <p class="muted">This sample asks for a small required baseline and one optional selective topic, so it demonstrates both “must have” and “nice to have” scopes.</p>
      </section>
    </div>
    {request_card}
    {projection_card}
  </main>
</body>
</html>"""


def create_sample_site_app(
    *,
    orf_service_url: str | None = None,
    demo_signer_key_path: str | Path | None = None,
    send_json_fn: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] = send_json,
) -> FastAPI:
    service_url = (orf_service_url or os.getenv("OPEN_RECOMMENDER_SERVICE_URL") or "http://127.0.0.1:8000").rstrip("/")
    signer_path = (
        Path(demo_signer_key_path)
        if demo_signer_key_path is not None
        else Path(os.environ["SAMPLE_SITE_DEMO_SIGNER_KEY_PATH"])
        if os.getenv("SAMPLE_SITE_DEMO_SIGNER_KEY_PATH")
        else None
    )
    app = FastAPI(title="Open News Demo Sample Site")
    app.state.sessions = {}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(
            _render_page(
                demo_signer_enabled=signer_path is not None,
            )
        )

    @app.get("/connect")
    def connect(profile_id: str) -> RedirectResponse:
        request_response = send_json_fn(
            "POST",
            f"{service_url}/profiles/{profile_id}/site-access-requests",
            {
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "required_scopes": [
                    "profile.read",
                    "topics.public",
                ],
                "optional_scopes": [
                    "topics.selective:orf:media/podcasts",
                ],
            },
        )
        request_id = request_response["access_request"]["request_id"]
        app.state.sessions[request_id] = {
            "profile_id": profile_id,
            "request": request_response,
            "projection": None,
        }
        return RedirectResponse(url=f"/session/{request_id}", status_code=303)

    @app.get("/session/{request_id}", response_class=HTMLResponse)
    def session_page(request_id: str) -> HTMLResponse:
        session = app.state.sessions.get(request_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown sample session.")
        request_status = send_json_fn("GET", f"{service_url}/site-access-requests/{request_id}")
        session["request"] = request_status
        return HTMLResponse(
            _render_page(
                profile_id=session["profile_id"],
                request_data=session["request"],
                request_status=request_status,
                projection=session.get("projection"),
                demo_signer_enabled=signer_path is not None,
            )
        )

    @app.post("/session/{request_id}/complete", response_class=HTMLResponse)
    def complete_session(request_id: str) -> HTMLResponse:
        session = app.state.sessions.get(request_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown sample session.")
        request_status = send_json_fn("GET", f"{service_url}/site-access-requests/{request_id}")
        if request_status["access_request"]["status"] != "approved":
            return HTMLResponse(
                _render_page(
                    profile_id=session["profile_id"],
                    request_data=session["request"],
                    request_status=request_status,
                    error_message="Approve the request in the localhost trust app before completing the sample sign-in.",
                    demo_signer_enabled=signer_path is not None,
                ),
                status_code=400,
            )
        if signer_path is None:
            return HTMLResponse(
                _render_page(
                    profile_id=session["profile_id"],
                    request_data=session["request"],
                    request_status=request_status,
                    error_message="This sample site is running without SAMPLE_SITE_DEMO_SIGNER_KEY_PATH, so it cannot finish the localhost demo signer step.",
                    demo_signer_enabled=False,
                ),
                status_code=400,
            )

        exchange_response = send_json_fn(
            "POST",
            f"{service_url}/site-access-requests/{request_id}/exchange",
        )

        # DEMO ONLY: in a real deployment, this signing step happens in the user's client.
        signer = _load_demo_signer(signer_path)
        signature = _encode_bytes(signer.sign(_canonical_json(exchange_response["challenge_payload"])))

        verify_response = send_json_fn(
            "POST",
            f"{service_url}/site-access-requests/{request_id}/verify",
            {
                "challenge_id": exchange_response["challenge"]["challenge_id"],
                "signature": signature,
            },
        )
        projection = send_json_fn(
            "GET",
            f"{service_url}/grant-sessions/{verify_response['session']['session_id']}/projection",
        )
        session["projection"] = projection
        session["request"] = request_status
        return HTMLResponse(
            _render_page(
                profile_id=session["profile_id"],
                request_data=session["request"],
                request_status=request_status,
                projection=projection,
                demo_signer_enabled=True,
            )
        )

    return app


app = create_sample_site_app()
