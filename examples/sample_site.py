from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from open_recommender.crypto import load_private_key, sign_payload
from open_recommender.partner_sdk import JsonSender, PartnerClient, PartnerSDKError
from open_recommender.service import require_local_browser


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
        request_id = escape(access_request["request_id"])
        action_html = (
            "<p class='muted'>Choose what to share in your ORF client, then check the decision here.</p>"
            "<div class='actions'>"
            f"<a class='button' href='{escape(request_data['consent_review_url'])}' target='_blank' rel='noopener noreferrer'>Open consent review <span class='muted-on-button'>(new tab)</span></a>"
            f"<a class='button secondary' href='/session/{request_id}'>Check approval status</a></div>"
        )
        if status == "approved":
            if projection is not None:
                action_html = "<p class='muted'>The previously shared preference preview is below.</p>"
            elif demo_signer_enabled:
                action_html = (
                    "<p class='muted'>You approved sharing. Use the local demo signer to finish the proof step.</p>"
                    f"<form method='post' action='/session/{request_id}/complete'>"
                    "<button type='submit'>Preview shared preferences</button>"
                    "</form>"
                )
            else:
                action_html = (
                    "<p class='muted'>Sharing is approved. Finish the proof step with your user-side signer or the local reference integration script. This site cannot sign for you.</p>"
                )
        elif status != "pending":
            action_html = (
                "<p class='muted'>This request is no longer usable. Start a new request if you want to try again.</p>"
                "<a class='button secondary' href='/'>Start again</a>"
            )
        status_label = {"pending": "Waiting for your decision", "approved": "Sharing approved",
                        "denied": "You declined", "expired": "Request expired"}.get(status, status)

        request_card = f"""
        <section class="card">
          <h2>Current request</h2>
          <p class="status">{escape(status_label)}</p>
          <p><strong>Purpose:</strong> {escape(access_request['purpose'])}</p>
          <details>
          <summary>Technical request details</summary>
          <p><strong>Request ID:</strong> <code>{request_id}</code></p>
          <p><strong>Required scopes:</strong> {escape(", ".join(access_request.get('required_scopes', []))) or "None"}</p>
          <p><strong>Optional scopes:</strong> {escape(", ".join(access_request.get('optional_scopes', []))) or "None"}</p>
          </details>
          {action_html}
        </section>
        """

    projection_card = ""
    if projection is not None:
        topic_items = "".join(
            f"<li>{escape(topic['topic'])} ({escape(topic['visibility'])})</li>"
            for topic in projection["projection"].get("topics", [])
        )
        projection_card = f"""
        <section class="card">
          <h2>Shared preference preview</h2>
          <p><strong>Display name:</strong> {escape(str(projection['projection'].get('display_name', '')))}</p>
          <p class="muted">These are the signals the service returned for this grant, not a ranked content feed.</p>
          <ul>{topic_items or "<li>No topics returned.</li>"}</ul>
          <p class="muted">This is a previously returned copy, not a live permission check. Revoking access stops new service reads; it cannot erase copies already received.</p>
        </section>
        """

    error_html = ""
    if error_message is not None:
        error_html = f"<div class='banner error' role='alert'><strong>Sample site error:</strong> {escape(error_message)}</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open News Demo sample site</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: radial-gradient(ellipse at 90% 0%, #12312f, transparent 42%), #080f1a; color: #e2e8f0; line-height: 1.6; }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; }}
    .brand {{ font-weight: 800; font-size: 1.3rem; letter-spacing: -.03em; }}
    .brand span, .eyebrow, h1 span {{ color: #6ee7b7; }}
    .preview-label {{ border: 1px solid #39514e; border-radius: 24px; padding: 6px 12px; color: #a7d9c9; font-size: .75rem; }}
    .hero {{ max-width: 740px; padding: 52px 0 30px; }}
    .hero.compact {{ padding: 24px 0 18px; }}
    .hero.compact h1 {{ font-size: clamp(2rem, 5vw, 3rem); }}
    .eyebrow {{ font-size: .75rem; text-transform: uppercase; letter-spacing: .13em; font-weight: 650; }}
    h1 {{ font-size: clamp(2.7rem, 7vw, 4.8rem); line-height: 1.05; letter-spacing: -.055em; margin: 18px 0; }}
    h2 {{ font-size: 1.15rem; margin: 0 0 12px; }}
    .lead {{ color: #b5c4d6; max-width: 580px; font-size: 1.05rem; }}
    .muted {{ color: #b5c4d6; font-size: .875rem; }}
    .journey {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; padding: 0; list-style: none; margin: 0 0 24px; color: #b5c4d6; font-size: .875rem; }}
    .journey li {{ border-top: 2px solid #39514e; padding-top: 12px; }}
    .journey strong {{ display: block; color: #e2e8f0; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }}
    .card {{ min-width: 0; background: #111c2b; border: 1px solid #2c3d50; border-radius: 18px; padding: 24px; margin-bottom: 16px; overflow-wrap: anywhere; }}
    .banner {{ margin-bottom: 20px; padding: 14px 18px; border-radius: 12px; }}
    .boundary {{ border-left: 3px solid #6ee7b7; background: #132520; color: #c6dbd3; font-size: .875rem; }}
    .warning {{ background: #451a03; border: 1px solid #b45309; }}
    .error {{ background: #450a0a; border: 1px solid #dc2626; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }}
    .button, button {{ display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 44px; border: 0; border-radius: 10px; padding: 10px 14px; background: #6ee7b7; color: #08251f; text-decoration: none; cursor: pointer; font-size: .875rem; font-weight: 650; }}
    .button.secondary {{ background: #334155; color: #f1f5f9; }}
    .muted-on-button {{ font-size: .75rem; }}
    .button:hover, button:hover {{ filter: brightness(1.08); }}
    :focus-visible {{ outline: 3px solid #93c5fd; outline-offset: 3px; }}
    input[type='text'] {{ width: 100%; min-height: 44px; margin-top: 8px; padding: 10px 12px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #e2e8f0; font: inherit; }}
    .status {{ color: #6ee7b7; font-weight: 650; }}
    summary {{ cursor: pointer; color: #b5c4d6; }}
    details {{ padding: 10px 0; }}
    @media (max-width: 560px) {{ main {{ padding: 20px 16px 40px; }} .hero {{ padding: 32px 0 22px; }} .card {{ padding: 18px; }} .journey {{ gap: 10px; font-size: .75rem; }} .preview-label {{ font-size: .7rem; }} }}
  </style>
</head>
<body>
  <main>
    <header><div class="brand"><span>ORF</span> / Open News</div><span class="preview-label">Local partner demo</span></header>
    <section class="hero{' compact' if request_data is not None else ''}">
      <p class="eyebrow">A site that asks first</p>
      <h1>Your preferences.<br><span>Your permission.</span></h1>
      <p class="lead">See what a site can use to personalize your experience. Connect a profile, choose what to share, and preview the approved signals.</p>
    </section>
    <ol class="journey" aria-label="Demo steps">
      <li><strong>01 / Connect</strong>Use your existing profile.</li>
      <li><strong>02 / Choose</strong>Review sharing in your ORF client.</li>
      <li><strong>03 / Preview</strong>See the signals returned to this site.</li>
    </ol>
    <aside class="banner boundary">Localhost preview, not a production sign-in. Your trusted ORF service stores readable profile history; this site receives only signals allowed by an approved grant.</aside>
    {banner}
    {error_html}
    {request_card}
    {projection_card}
    <div class="grid">
      <section class="card">
        <h2>Start a sample session</h2>
        <p class="muted" id="profile-help">Copy a profile ID from your local ORF client. It must already exist in the service.</p>
        <form method="get" action="/connect">
          <label>
            <strong>Profile ID</strong>
            <input type="text" name="profile_id" value="{escape(profile_id or '')}" placeholder="orf:profile:..." required autocomplete="off" aria-describedby="profile-help">
          </label>
          <p style="margin-top: 16px;"><button type="submit">Request personalized access</button></p>
        </form>
      </section>
      <section class="card">
        <h2>What stays in your hands</h2>
        <ul class="muted">
          <li><strong>You</strong> review sharing and authorize it in your own ORF client.</li>
          <li><strong>This site</strong> asks for specific signals. Private topics stay out of its projection.</li>
          <li><strong>The demo signer</strong> exists only for localhost validation and must not be copied into a real deployment.</li>
        </ul>
        <p class="muted">This sample asks for basic profile details and public topics, with podcast preferences as an optional extra.</p>
      </section>
    </div>
  </main>
</body>
</html>"""


def create_sample_site_app(
    *,
    orf_service_url: str | None = None,
    demo_signer_key_path: str | Path | None = None,
    site_token: str | None = None,
    send_json_fn: JsonSender | None = None,
) -> FastAPI:
    service_url = (orf_service_url or os.getenv("OPEN_RECOMMENDER_SERVICE_URL") or "http://127.0.0.1:8000").rstrip("/")
    signer_path = (
        Path(demo_signer_key_path)
        if demo_signer_key_path is not None
        else Path(os.environ["SAMPLE_SITE_DEMO_SIGNER_KEY_PATH"])
        if os.getenv("SAMPLE_SITE_DEMO_SIGNER_KEY_PATH")
        else None
    )
    token = site_token if site_token is not None else os.getenv("ORF_SITE_TOKEN")
    partner = PartnerClient(service_url, site_id="open-news-demo" if token is not None else None,
                            site_token=token, send_json=send_json_fn)
    app = FastAPI(title="Open News Demo Sample Site", dependencies=[Depends(require_local_browser)])
    # ponytail: process-local demo sessions; hosted sites need user-bound sessions and retention.
    app.state.sessions = {}

    @app.middleware("http")
    async def prevent_caching(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PartnerSDKError)
    async def partner_error(request: Request, error: PartnerSDKError):
        return HTMLResponse(_render_page(demo_signer_enabled=signer_path is not None,
            error_message="The ORF service rejected the request or is unavailable. Check the backend configuration."),
            status_code=502)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(
            _render_page(
                demo_signer_enabled=signer_path is not None,
            )
        )

    @app.get("/connect")
    def connect(profile_id: str) -> RedirectResponse:
        request_response = partner.create_access_request(
            profile_id=profile_id,
            site_id="open-news-demo",
            purpose="Personalize the pilot site feed.",
            required_scopes=["profile.read", "topics.public"],
            optional_scopes=["topics.selective:orf:media/podcasts"],
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
        request_status = partner.get_access_request(request_id)
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
        request_status = partner.get_access_request(request_id)
        if request_status["access_request"]["status"] != "approved":
            return HTMLResponse(
                _render_page(
                    profile_id=session["profile_id"],
                    request_data=session["request"],
                    request_status=request_status,
                    error_message="Approve the request in your localhost ORF client before requesting this preview.",
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

        exchange_response = partner.exchange_access_request(request_id)

        # DEMO ONLY: in a real deployment, this signing step happens in the user's client.
        signature = sign_payload(exchange_response["challenge_payload"], load_private_key(signer_path))

        verify_response = partner.verify_access_request(
            request_id=request_id,
            challenge_id=exchange_response["challenge"]["challenge_id"],
            signature=signature,
        )
        projection = partner.get_projection(verify_response['session']['session_id'])
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
