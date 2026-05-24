from __future__ import annotations

import hashlib
import hmac
import json
import os
from html import escape
from pathlib import Path
import time
from typing import Any
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .models import ORFProfile, SignedEvent
from .store import SQLiteStore


@dataclass(frozen=True)
class ServiceConfig:
    db_path: str
    admin_token: str | None
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    pilot_sites_path: str | None
    sync_token: str | None


class FixedWindowRateLimiter:
    def __init__(self, *, window_seconds: int, max_requests: int) -> None:
        if window_seconds <= 0:
            raise ValueError("Rate limit window must be positive.")
        if max_requests <= 0:
            raise ValueError("Rate limit max requests must be positive.")
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._buckets: dict[str, tuple[float, int]] = {}

    def check(self, bucket: str, client_id: str) -> None:
        key = f"{bucket}:{client_id}"
        now = time.monotonic()
        reset_at, count = self._buckets.get(key, (now + self.window_seconds, 0))
        if now >= reset_at:
            reset_at = now + self.window_seconds
            count = 0
        if count >= self.max_requests:
            retry_after_seconds = max(1, int(reset_at - now))
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Rate limit exceeded.",
                    "bucket": bucket,
                    "retry_after_seconds": retry_after_seconds,
                },
            )
        self._buckets[key] = (reset_at, count + 1)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"Environment variable {name} must be an integer.") from error


def _service_config(
    db_path: str | Path | None,
    *,
    admin_token: str | None,
    rate_limit_window_seconds: int | None,
    rate_limit_max_requests: int | None,
    pilot_sites_path: str | Path | None,
    sync_token: str | None,
) -> ServiceConfig:
    resolved_db_path = str(db_path or os.getenv("OPEN_RECOMMENDER_DB_PATH", "open_recommender.db"))
    resolved_admin_token = admin_token if admin_token is not None else os.getenv("OPEN_RECOMMENDER_ADMIN_TOKEN")
    resolved_window = (
        rate_limit_window_seconds
        if rate_limit_window_seconds is not None
        else _int_env("OPEN_RECOMMENDER_RATE_LIMIT_WINDOW_SECONDS", 60)
    )
    resolved_max_requests = (
        rate_limit_max_requests
        if rate_limit_max_requests is not None
        else _int_env("OPEN_RECOMMENDER_RATE_LIMIT_MAX_REQUESTS", 20)
    )
    resolved_pilot_sites_path = (
        str(pilot_sites_path)
        if pilot_sites_path is not None
        else os.getenv("OPEN_RECOMMENDER_PILOT_SITES_PATH")
    )
    resolved_sync_token = sync_token if sync_token is not None else os.getenv("OPEN_RECOMMENDER_SYNC_TOKEN")
    return ServiceConfig(
        db_path=resolved_db_path,
        admin_token=resolved_admin_token,
        rate_limit_window_seconds=resolved_window,
        rate_limit_max_requests=resolved_max_requests,
        pilot_sites_path=resolved_pilot_sites_path,
        sync_token=resolved_sync_token,
    )


def _pilot_sites_from_path(path: str | Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Pilot sites config must be a JSON array.")
    sites: list[dict[str, Any]] = []
    required = {"site_id", "site_name", "allowed_scopes"}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Pilot site entry at index {index} must be an object.")
        if not required.issubset(set(item.keys())):
            missing = sorted(required - set(item.keys()))
            raise ValueError(
                f"Pilot site entry at index {index} is missing required fields: {', '.join(missing)}."
            )
        sites.append(dict(item))
    return tuple(sites)


def _humanize_topic(topic: str) -> str:
    _, _, path = topic.partition(":")
    return path.replace("/", " / ").replace("-", " ")


def _build_demo_personalization(public_profile: dict[str, Any], *, verified: bool) -> dict[str, Any]:
    public_topics = sorted(
        public_profile["topics"],
        key=lambda item: (-float(item["weight"]), str(item["topic"])),
    )
    recommendations: list[dict[str, Any]] = []
    for item in public_topics[:3]:
        topic = str(item["topic"])
        slug = topic.partition(":")[2].replace("/", "-")
        recommendations.append(
            {
                "item_id": f"demo-{slug}",
                "title": f"{_humanize_topic(topic).title()} picks",
                "why": f"Derived from public ORF topic {topic} (weight {float(item['weight']):.1f}).",
                "stage": "verified" if verified else "public-preview",
            }
        )

    if recommendations:
        summary = "Immediate personalization from the portable ORF profile."
    else:
        summary = "No public topics yet, so the demo falls back to a neutral starter feed."
        recommendations.append(
            {
                "item_id": "demo-starter-feed",
                "title": "Starter feed",
                "why": "Shown when the portable profile has no public topics yet.",
                "stage": "verified" if verified else "public-preview",
            }
        )

    if verified:
        summary += " Challenge proof verified, so the site can keep this personalized session without a site-specific account."
    else:
        summary += " The site can already tailor content before any site-specific sign-up."

    return {
        "mode": "verified-profile" if verified else "public-profile",
        "summary": summary,
        "featured_topics": [item["topic"] for item in public_topics[:3]],
        "recommendations": recommendations,
    }


def _build_demo_response(profile: ORFProfile, *, verified: bool) -> dict[str, Any]:
    public_profile = profile.public_projection()
    return {
        "demo": {
            "site": "open-news-demo",
            "site_account_required": False,
            "verified": verified,
            "proof": "challenge-signature" if verified else None,
        },
        "viewer": {
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "public_topic_count": len(public_profile["topics"]),
        },
        "public_profile": public_profile,
        "personalization": _build_demo_personalization(public_profile, verified=verified),
        "session": {
            "portable_profile_session": verified,
            "can_save_state_without_account": verified,
        },
    }


def _scope_label(scope: str) -> str:
    if scope == "profile.read":
        return "Basic profile identity"
    if scope == "topics.public":
        return "Public topics already shareable to sites"
    if scope == "consent.summary":
        return "High-level sharing preferences"
    if scope.startswith("topics.selective:"):
        return f"Selected topic: {_humanize_topic(scope.split(':', 2)[2]).title()}"
    return scope


def _scope_description(scope: str) -> str:
    if scope == "profile.read":
        return "Lets the site identify this portable profile without creating a site-specific account."
    if scope == "topics.public":
        return "Lets the site read topics you already expose in your public ORF view."
    if scope == "consent.summary":
        return "Lets the site see a small summary of sharing-related consent settings."
    if scope.startswith("topics.selective:"):
        return "Lets the site read one specific selective topic only if you approve it here."
    return "Custom scope."


def _preview_topic_reason(topic: dict[str, Any]) -> str:
    visibility = str(topic.get("visibility", ""))
    if visibility == "public":
        return "Shared because it is public and public-topic sharing is enabled."
    if visibility == "selective":
        return "Shared only because this request explicitly asks for that selective topic."
    return "Shared by scope."


def _render_projection_preview(projection: dict[str, Any] | None) -> str:
    if projection is None:
        return "<p><strong>Preview unavailable.</strong> This server does not have the profile data needed to show what the site would see.</p>"

    sections: list[str] = [
        "<div class='card'><h2>What this site could see</h2>",
        f"<p><strong>Display name:</strong> {escape(str(projection.get('display_name', 'Unknown')))}</p>",
    ]

    topics = projection.get("topics", [])
    if topics:
        topic_items = "".join(
            f"<li><strong>{escape(_humanize_topic(str(topic['topic'])).title())}</strong> "
            f"<span class='badge'>{escape(str(topic['visibility']))}</span><br>"
            f"<span class='muted'>{escape(_preview_topic_reason(topic))}</span></li>"
            for topic in topics
        )
        sections.append(f"<h3>Topics</h3><ul>{topic_items}</ul>")
    else:
        sections.append("<p>No topics would be shared from this request.</p>")

    consent = projection.get("consent")
    if consent:
        consent_items = "".join(
            f"<li><strong>{escape(key.replace('_', ' ').title())}:</strong> {escape(str(value).lower())}</li>"
            for key, value in consent.items()
        )
        sections.append(f"<h3>Consent summary</h3><ul>{consent_items}</ul>")

    sections.append("</div>")
    return "".join(sections)


def _scope_groups(
    required_scope_list: list[str],
    optional_scope_list: list[str],
) -> tuple[list[str], list[str], list[str]]:
    optional_already_public = [
        scope
        for scope in optional_scope_list
        if scope in {"profile.read", "topics.public", "consent.summary"}
    ]
    optional_newly_shared = [
        scope for scope in optional_scope_list if scope.startswith("topics.selective:")
    ]
    return required_scope_list, optional_already_public, optional_newly_shared


def _render_scope_group(
    title: str,
    hint: str,
    scopes: list[str],
    *,
    checked: bool = True,
    disabled: bool = False,
) -> str:
    if not scopes:
        return ""
    checked_attr = " checked" if checked else ""
    disabled_attr = " disabled" if disabled else ""
    items = []
    for scope in scopes:
        items.append(
            "<label class='scope-row'>"
            f"<input type='checkbox' name='approved-scope' value='{escape(scope)}'{checked_attr}{disabled_attr}>"
            "<span>"
            f"<strong>{escape(_scope_label(scope))}</strong><br>"
            f"<span class='muted'>{escape(_scope_description(scope))}</span>"
            "</span>"
            "</label>"
            )
    return (
        "<div class='scope-group'>"
        f"<h3>{escape(title)}</h3>"
        f"<p class='muted'>{escape(hint)}</p>"
        f"{''.join(items)}"
        "</div>"
    )


def _render_scope_review(
    required_scope_list: list[str],
    optional_scope_list: list[str],
    *,
    checked: bool = True,
) -> str:
    required_scopes, optional_already_public, optional_newly_shared = _scope_groups(
        required_scope_list,
        optional_scope_list,
    )
    sections = [
        _render_scope_group(
            "Required to continue",
            "These scopes are mandatory for this sign-in flow. If you do not want to share them, deny the request instead of trying to uncheck them.",
            required_scopes,
            checked=True,
            disabled=True,
        ),
        _render_scope_group(
            "Optional already-public or identity-level",
            "These items are optional extras. They cover public or identity-level data you can still skip.",
            optional_already_public,
            checked=checked,
        ),
        _render_scope_group(
            "Optional newly shared with this site only",
            "These selective topics are not public. They only become visible to this site if you keep them checked.",
            optional_newly_shared,
            checked=checked,
        ),
    ]
    return "".join(section for section in sections if section)


def _scope_group_payload(
    required_scope_list: list[str],
    optional_scope_list: list[str],
    *,
    checked: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    required_scopes, optional_already_public, optional_newly_shared = _scope_groups(
        required_scope_list,
        optional_scope_list,
    )

    def _serialize(scopes: list[str], *, required: bool = False, disabled: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "scope": scope,
                "label": _scope_label(scope),
                "description": _scope_description(scope),
                "checked": True if required else checked,
                "required": required,
                "disabled": disabled,
            }
            for scope in scopes
        ]

    return {
        "required": _serialize(required_scopes, required=True, disabled=True),
        "optional_already_public": _serialize(optional_already_public),
        "optional_newly_shared": _serialize(optional_newly_shared),
    }


def _render_trust_nav(*, active: str) -> str:
    links = [
        ("Profile Lens", "/lens", "lens"),
        ("Consent Inbox", "/consent", "consent"),
        ("Site Grants", "/consent/grants", "grants"),
    ]
    items = []
    for label, href, key in links:
        current = " nav-link-active" if key == active else ""
        items.append(f"<a class='nav-link{current}' href='{href}'>{escape(label)}</a>")
    return f"<nav class='trust-nav'>{''.join(items)}</nav>"


def _lens_profile_payload(profile: ORFProfile) -> dict[str, Any]:
    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "display_name": profile.display_name,
        "topics": [
            {
                "topic": topic.topic,
                "weight": topic.weight,
                "visibility": topic.visibility.value,
            }
            for topic in sorted(profile.topics.values(), key=lambda item: item.topic)
        ],
        "opt_out_topics": sorted(profile.opt_out_topics),
        "consent": {
            "share_public_topics": profile.consent.share_public_topics,
            "ad_personalization": profile.consent.ad_personalization,
            "hosted_sync": profile.consent.hosted_sync,
        },
        "updated_at": profile.updated_at,
    }


def _render_consent_index_page(
    *,
    pending_requests: list[dict[str, Any]],
    profile_id: str | None,
) -> str:
    filter_hint = ""
    if profile_id is not None:
        filter_hint = f"<p class='muted'>Showing pending requests for profile <strong>{escape(profile_id)}</strong>.</p>"

    if pending_requests:
        items = []
        for item in pending_requests:
            access_request = item["access_request"]
            items.append(
                "<div class='request-row'>"
                f"<div><strong>{escape(str(access_request.get('site_name', 'Unknown site')))}</strong><br>"
                f"<span class='muted'>{escape(str(access_request.get('purpose', 'No purpose provided.')))}</span><br>"
                f"<span class='muted'>Profile: {escape(str(item['profile_id']))}</span></div>"
                "<div class='request-actions'>"
                f"<a class='button secondary' href='/lens?profile_id={escape(str(item['profile_id']))}'>Open profile lens</a>"
                f"<a class='button' href='/consent/site-access-requests/{escape(str(access_request['request_id']))}'>Review request</a>"
                "</div>"
                "</div>"
            )
        requests_html = "".join(items)
    else:
        requests_html = (
            "<div class='card'><h2>No pending requests</h2>"
            "<p class='muted'>When a pilot site asks for access, the request will appear here for browser review.</p>"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Recommender consent inbox</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 24px 20px 48px; }}
    .trust-nav {{ display: flex; gap: 12px; margin-bottom: 24px; }}
    .nav-link {{ color: #cbd5e1; text-decoration: none; padding: 10px 14px; border: 1px solid #334155; border-radius: 999px; }}
    .nav-link-active {{ background: #1d4ed8; border-color: #1d4ed8; color: white; }}
    .lead, .muted {{ color: #94a3b8; }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
    .request-row {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; margin-top: 12px; }}
    .request-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .button {{ display: inline-block; text-decoration: none; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; }}
    .button.secondary {{ background: #334155; }}
    @media (max-width: 720px) {{ .request-row {{ flex-direction: column; align-items: flex-start; }} }}
  </style>
</head>
<body>
  <main>
    {_render_trust_nav(active='consent')}
    <h1>Consent Inbox</h1>
    <p class="lead">This localhost-only inbox shows pending site requests that need a user decision.</p>
    {filter_hint}
    {requests_html}
  </main>
</body>
</html>"""


def _render_grants_page(
    *,
    grants: list[dict[str, Any]],
    csrf_tokens: dict[str, str],
    profile_id: str | None,
) -> str:
    filter_hint = ""
    if profile_id is not None:
        filter_hint = f"<p class='muted'>Showing grants for profile <strong>{escape(profile_id)}</strong>.</p>"

    if grants:
        rows = []
        for item in grants:
            grant = item["grant"]
            grant_id = str(grant["grant_id"])
            status = str(item["status"])
            revoke_button = "<span class='muted'>No revoke action available.</span>"
            if status == "active":
                revoke_button = (
                    f"<button class='revoke-button' data-grant-id='{escape(grant_id)}' "
                    f"data-csrf-token='{escape(csrf_tokens[grant_id])}'>Revoke grant</button>"
                )
            rows.append(
                "<div class='grant-row'>"
                f"<div><strong>{escape(str(item['site_name']))}</strong><br>"
                f"<span class='muted'>Grant: {escape(grant_id)}</span><br>"
                f"<span class='muted'>Profile: {escape(str(item['profile_id']))}</span><br>"
                f"<span class='muted'>Scopes: {escape(', '.join(str(scope) for scope in grant['approved_scopes']))}</span><br>"
                f"<span class='status status-{escape(status)}'>{escape(status.title())}</span>"
                "</div>"
                "<div class='grant-actions'>"
                f"{revoke_button}"
                f"<a class='button secondary' href='/site-access-requests/{escape(str(grant['request_id']))}'>View request JSON</a>"
                "</div>"
                "</div>"
            )
        grants_html = "".join(rows)
    else:
        grants_html = (
            "<div class='card'><h2>No grants yet</h2>"
            "<p class='muted'>Approved site requests will appear here, and active grants can be revoked from this page.</p>"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Recommender site grants</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 24px 20px 48px; }}
    .trust-nav {{ display: flex; gap: 12px; margin-bottom: 24px; }}
    .nav-link {{ color: #cbd5e1; text-decoration: none; padding: 10px 14px; border: 1px solid #334155; border-radius: 999px; }}
    .nav-link-active {{ background: #1d4ed8; border-color: #1d4ed8; color: white; }}
    .lead, .muted {{ color: #94a3b8; }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
    .grant-row {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; margin-top: 12px; }}
    .grant-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .button, .revoke-button {{ display: inline-block; text-decoration: none; border-radius: 10px; padding: 10px 14px; border: 0; background: #2563eb; color: white; cursor: pointer; }}
    .button.secondary {{ background: #334155; }}
    .status {{ display: inline-block; margin-top: 8px; border-radius: 999px; padding: 2px 8px; font-size: 12px; }}
    .status-active {{ background: #052e16; color: #86efac; }}
    .status-revoked {{ background: #450a0a; color: #fca5a5; }}
    .status-expired {{ background: #422006; color: #fcd34d; }}
    #result {{ margin-top: 16px; padding: 12px; border-radius: 10px; border: 1px solid #334155; background: #111827; }}
    @media (max-width: 720px) {{ .grant-row {{ flex-direction: column; align-items: flex-start; }} }}
  </style>
</head>
<body>
  <main>
    {_render_trust_nav(active='grants')}
    <h1>Site Grants</h1>
    <p class="lead">This localhost-only page shows approved grants and lets you revoke active grants to stop future session exchanges.</p>
    {filter_hint}
    <div class="card">
      <h2>Revocation behavior</h2>
      <p class="muted">Revoking a grant blocks future exchange attempts for that grant. Sites must request access again to regain access.</p>
    </div>
    {grants_html}
    <div id="result" class="muted">No revocation action yet.</div>
  </main>
  <script>
    const result = document.getElementById("result");
    async function revokeGrant(grantId, csrfToken) {{
      const response = await fetch(`/consent/grants/${{encodeURIComponent(grantId)}}/revoke`, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-Open-Recommender-CSRF-Token": csrfToken,
        }},
        body: JSON.stringify({{ actor: "browser-consent-ui", reason: "User revoked grant from grants page." }}),
      }});
      const body = await response.json();
      if (!response.ok) {{
        result.textContent = JSON.stringify(body, null, 2);
        return;
      }}
      result.innerHTML = "<strong>Grant revoked.</strong> Refresh this page to see updated status and use /admin/audit-events to inspect the recorded audit event.";
      document.querySelectorAll(`button[data-grant-id='${{grantId}}']`).forEach((button) => button.disabled = true);
    }}
    document.querySelectorAll(".revoke-button").forEach((button) => {{
      button.addEventListener("click", () => revokeGrant(button.dataset.grantId, button.dataset.csrfToken));
    }});
  </script>
</body>
</html>"""


def _render_consent_review_page(
    *,
    request_id: str,
    csrf_token: str,
    access_request: dict[str, Any],
    projection_preview: dict[str, Any] | None,
    profile_id: str,
) -> str:
    ignored_scopes = access_request.get("ignored_requested_scopes", [])
    ignored_html = ""
    if ignored_scopes:
        ignored_items = "".join(f"<li>{escape(str(scope))}</li>" for scope in ignored_scopes)
        ignored_html = (
            "<div class='card warning'><h3>Ignored scope requests</h3>"
            "<p>These values are not part of the current reference contract, so they will not be approved here.</p>"
            f"<ul>{ignored_items}</ul></div>"
        )

    requested_scopes = [str(scope) for scope in access_request.get("requested_scopes", [])]
    required_scopes = [str(scope) for scope in access_request.get("required_scopes", [])]
    optional_scopes = [str(scope) for scope in access_request.get("optional_scopes", [])]
    status = str(access_request.get("status", "unknown"))
    action_block = ""
    if status == "pending":
        action_block = f"""
        <div class="card">
          <h2>Decide what this site can see</h2>
          <p class="muted">Required scopes keep the site's requested sign-in contract intact. Optional scopes are the extra data you can remove before approving.</p>
          <div class="scope-list">{_render_scope_review(required_scopes, optional_scopes)}</div>
          <label class="deny-reason">
            <span>Deny reason (optional)</span>
            <input id="deny-reason" type="text" placeholder="Why are you declining this request?">
          </label>
          <div class="actions">
            <button id="approve-button" onclick="approveRequest()">Approve selected scopes</button>
            <button id="deny-button" class="secondary" onclick="denyRequest()">Deny request</button>
          </div>
          <div id="result" class="result muted"></div>
        </div>
        """
    else:
        action_block = (
            "<div class='card'><h2>Request status</h2>"
            f"<p>This request is already <strong>{escape(status)}</strong>. Browser actions are disabled.</p>"
            "<p><a class='link-button secondary' href='/consent'>Back to consent inbox</a></p>"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Recommender consent review</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 24px 20px 48px; }}
    h1, h2, h3 {{ margin-bottom: 0.5rem; }}
    .lead {{ color: #cbd5e1; margin-bottom: 24px; }}
    .trust-nav {{ display: flex; gap: 12px; margin-bottom: 24px; }}
    .nav-link {{ color: #cbd5e1; text-decoration: none; padding: 10px 14px; border: 1px solid #334155; border-radius: 999px; }}
    .nav-link-active {{ background: #1d4ed8; border-color: #1d4ed8; color: white; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
    .card {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
    .warning {{ border-color: #b45309; }}
    .muted {{ color: #94a3b8; }}
    .scope-list {{ display: grid; gap: 10px; margin: 16px 0; }}
    .scope-group {{ border-top: 1px solid #1e293b; padding-top: 12px; }}
    .scope-group:first-child {{ border-top: 0; padding-top: 0; }}
    .scope-row {{ display: grid; grid-template-columns: 20px 1fr; gap: 12px; align-items: start; }}
    .badge {{ display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px; background: #1e293b; color: #93c5fd; font-size: 12px; }}
    .actions {{ display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }}
    button {{ border: 0; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; cursor: pointer; font-size: 14px; }}
    button.secondary {{ background: #334155; }}
    input[type='text'] {{ width: 100%; margin-top: 6px; padding: 10px 12px; border-radius: 10px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #020617; padding: 12px; border-radius: 10px; border: 1px solid #1e293b; min-height: 56px; }}
    .result {{ margin-top: 16px; padding: 12px; border-radius: 10px; border: 1px solid #1e293b; background: #020617; min-height: 24px; }}
    .link-button {{ display: inline-block; text-decoration: none; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; }}
    .link-button.secondary {{ background: #334155; }}
    ul {{ padding-left: 20px; }}
  </style>
</head>
<body>
  <main>
    {_render_trust_nav(active='consent')}
    <h1>Review site access request</h1>
    <p class="lead">This localhost-only review page shows what <strong>{escape(str(access_request.get('site_name', 'This site')))}</strong> is asking for and what it could see if you approve it.</p>
    <div class="grid">
      <div class="card">
        <h2>Request details</h2>
        <p><strong>Site:</strong> {escape(str(access_request.get('site_name', 'Unknown site')))}</p>
        <p><strong>Purpose:</strong> {escape(str(access_request.get('purpose', 'No purpose provided.')))}</p>
        <p><strong>Status:</strong> {escape(status)}</p>
        <p><strong>Required scopes:</strong> {escape(', '.join(required_scopes) or 'None')}</p>
        <p><strong>Optional scopes:</strong> {escape(', '.join(optional_scopes) or 'None')}</p>
        <p><strong>Profile:</strong> {escape(profile_id)}</p>
      </div>
      <div class="card">
        <h2>How to read this page</h2>
        <p class="muted">Required scopes are all-or-nothing for this request. Optional scopes are the parts you can keep or remove. Private topics stay on this device either way.</p>
      </div>
    </div>
    {ignored_html}
    <div class="grid" style="margin-top: 16px;">
      {action_block}
      {_render_projection_preview(projection_preview)}
    </div>
  </main>
  <script>
    const csrfToken = {json.dumps(csrf_token)};
    const approveUrl = {json.dumps(f"/consent/site-access-requests/{request_id}/approve")};
    const denyUrl = {json.dumps(f"/consent/site-access-requests/{request_id}/deny")};
    const result = document.getElementById("result");
    const profileId = {json.dumps(profile_id)};
    function selectedScopes() {{
      return Array.from(document.querySelectorAll("input[name='approved-scope']:checked")).map((input) => input.value);
    }}
    async function sendDecision(url, payload) {{
      const response = await fetch(url, {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "X-Open-Recommender-CSRF-Token": csrfToken
        }},
        body: JSON.stringify(payload)
      }});
      const body = await response.json();
      if (response.ok) {{
        document.querySelectorAll("button, input[type='checkbox'], input[type='text']").forEach((node) => node.disabled = true);
        const action = body.access_request?.status === "denied" ? "denied" : "approved";
        const nextStep = action === "approved"
          ? "The site can now complete the challenge-based exchange flow."
          : "The request is closed and the site cannot continue this access flow.";
        result.innerHTML = `
          <strong>Request ${{action}}.</strong><br>
          <span class="muted">${{nextStep}}</span><br><br>
          <a class="link-button secondary" href="/consent">Back to consent inbox</a>
          <a class="link-button" href="/lens?profile_id=${{encodeURIComponent(profileId)}}">Open this profile in the lens</a>
        `;
        return;
      }}
      result.textContent = JSON.stringify(body, null, 2);
    }}
    function approveRequest() {{
      sendDecision(approveUrl, {{
        approved_scopes: selectedScopes(),
        actor: "browser-consent-ui"
      }});
    }}
    function denyRequest() {{
      sendDecision(denyUrl, {{
        reason: document.getElementById("deny-reason")?.value || null,
        actor: "browser-consent-ui"
      }});
    }}
  </script>
</body>
</html>"""


def _render_profile_lens_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Recommender profile lens</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #020617; color: #e2e8f0; }
    main { max-width: 1100px; margin: 0 auto; padding: 24px 20px 48px; }
    h1, h2, h3 { margin-bottom: 0.5rem; }
    .lead, .muted { color: #94a3b8; }
    .trust-nav { display: flex; gap: 12px; margin-bottom: 24px; }
    .nav-link { color: #cbd5e1; text-decoration: none; padding: 10px 14px; border: 1px solid #334155; border-radius: 999px; }
    .nav-link-active { background: #1d4ed8; border-color: #1d4ed8; color: white; }
    .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    .card { background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 18px; }
    .topic { border: 1px solid #334155; border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }
    .tag { display: inline-block; border-radius: 999px; background: #1e293b; padding: 2px 8px; font-size: 12px; margin-left: 8px; color: #93c5fd; }
    .controls { display: grid; gap: 12px; }
    input[type='file'], select { width: 100%; margin-top: 6px; padding: 10px 12px; border-radius: 10px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; }
    button { border: 0; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; cursor: pointer; font-size: 14px; }
    button.secondary { background: #334155; }
    .button-link { display: inline-block; text-decoration: none; border-radius: 10px; padding: 10px 14px; background: #2563eb; color: white; }
    .scope-list { display: grid; gap: 10px; margin-top: 12px; }
    .scope-group { border-top: 1px solid #1e293b; padding-top: 12px; }
    .scope-group:first-child { border-top: 0; padding-top: 0; }
    .scope-row { display: grid; grid-template-columns: 20px 1fr; gap: 10px; align-items: start; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 12px; }
  </style>
</head>
<body>
  <main>
    NAV_PLACEHOLDER
    <h1>Local Profile Lens</h1>
    <p class="lead">Load a local ORF file or a profile already registered in this local service. The browser computes the trust views directly so your file is not posted back to the service just to preview scopes.</p>
    <div class="grid">
      <section class="card">
        <h2>Choose a profile source</h2>
        <div class="controls">
          <label>
            <strong>Open a local .orf file</strong>
            <input id="profile-file" type="file" accept=".orf,application/json">
          </label>
          <label>
            <strong>Or load a profile already in this local service</strong>
            <select id="stored-profile-select">
              <option value="">Choose a stored profile</option>
            </select>
          </label>
          <button id="load-stored-button" type="button">Load stored profile</button>
          <p class="muted" id="source-status">No profile loaded yet.</p>
          <div id="local-file-actions" style="display:none;">
            <p class="muted" id="register-status">This local file is only loaded in the browser until you register it with this local service.</p>
            <button id="register-local-profile" type="button" class="secondary">Register or update in local service</button>
          </div>
        </div>
      </section>
      <section class="card">
        <h2>How to read this</h2>
        <p class="muted">This page answers three questions: what stays on this device, what is already public, and what a site could see if you approved specific scopes.</p>
        <ul class="muted">
          <li><strong>Private</strong> topics stay on this device.</li>
          <li><strong>Public</strong> topics can appear in the public view when public-topic sharing is enabled.</li>
          <li><strong>Selective</strong> topics only appear in the site view if you choose them.</li>
        </ul>
      </section>
    </div>

    <div id="lens-root" style="display:none; margin-top: 20px;">
      <div class="grid">
        <section class="card">
          <h2>Your profile</h2>
          <pre id="profile-summary"></pre>
        </section>
        <section class="card">
          <h2>Site preview controls</h2>
          <p class="muted">Imagine a site asking for these scopes. Identity and already-public items are separate from selective topics that would be newly shared with one site only.</p>
          <div id="scope-list" class="scope-list"></div>
        </section>
      </div>
      <div class="grid" style="margin-top: 16px;">
        <section class="card">
          <h2>What stays on this device</h2>
          <div id="private-topics"></div>
        </section>
        <section class="card">
          <h2>What is public</h2>
          <div id="public-topics"></div>
        </section>
        <section class="card">
          <h2>What this site can see</h2>
          <div id="site-preview"></div>
        </section>
      </div>
      <div class="grid" style="margin-top: 16px;">
        <section class="card">
          <h2>Review pending site requests</h2>
          <p class="muted">If this profile already has live requests in the local service, review them here.</p>
          <div id="pending-requests"><p class="muted">Load a profile to see pending requests.</p></div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const state = { profile: null, localProfileDocument: null };
    const initialProfileId = new URLSearchParams(window.location.search).get("profile_id");

    function humanizeTopic(topic) {
      const parts = topic.split(":");
      const path = parts.length > 1 ? parts.slice(1).join(":") : topic;
      return path.replaceAll("/", " / ").replaceAll("-", " ");
    }

    function renderTopicList(containerId, topics, reasonBuilder) {
      const container = document.getElementById(containerId);
      if (!topics.length) {
        container.innerHTML = "<p class='muted'>Nothing to show.</p>";
        return;
      }
      container.innerHTML = topics.map((topic) => `
        <div class="topic">
          <strong>${humanizeTopic(topic.topic)}</strong>
          <span class="tag">${topic.visibility}</span>
          <div class="muted">${reasonBuilder(topic)}</div>
        </div>
      `).join("");
    }

    function computePublicTopics(profile) {
      if (!profile.consent.share_public_topics) {
        return [];
      }
      return profile.topics.filter((topic) => topic.visibility === "public" && !profile.opt_out_topics.includes(topic.topic));
    }

    function computeSitePreview(profile, scopes) {
      const scopeSet = new Set(scopes);
      const selectiveScopes = new Set(
        scopes.filter((scope) => scope.startsWith("topics.selective:")).map((scope) => scope.slice("topics.selective:".length))
      );
      const topics = profile.topics.filter((topic) => {
        if (profile.opt_out_topics.includes(topic.topic)) {
          return false;
        }
        if (topic.visibility === "private") {
          return false;
        }
        if (topic.visibility === "public") {
          return scopeSet.has("topics.public") && profile.consent.share_public_topics;
        }
        return selectiveScopes.has(topic.topic);
      });
      return {
        display_name: scopeSet.has("profile.read") ? profile.display_name : null,
        topics,
        consent: scopeSet.has("consent.summary")
          ? {
              share_public_topics: profile.consent.share_public_topics,
              ad_personalization: profile.consent.ad_personalization
            }
          : null
      };
    }

    function selectedScopes() {
      return Array.from(document.querySelectorAll("#scope-list input[type='checkbox']:checked")).map((node) => node.dataset.scope);
    }

    function renderSitePreview() {
      if (!state.profile) {
        return;
      }
      const preview = computeSitePreview(state.profile, selectedScopes());
      const container = document.getElementById("site-preview");
      const sections = [];
      if (preview.display_name) {
        sections.push(`<p><strong>Display name:</strong> ${preview.display_name}</p>`);
      }
      if (preview.consent) {
        sections.push(`<pre>${JSON.stringify(preview.consent, null, 2)}</pre>`);
      }
      if (preview.topics.length) {
        sections.push(preview.topics.map((topic) => `
          <div class="topic">
            <strong>${humanizeTopic(topic.topic)}</strong>
            <span class="tag">${topic.visibility}</span>
          </div>
        `).join(""));
      } else {
        sections.push("<p class='muted'>With the current scope choices, this site would not see any topics.</p>");
      }
      container.innerHTML = sections.join("");
    }

    function renderScopeGroup(title, hint, items) {
      if (!items.length) {
        return "";
      }
      return `
        <div class="scope-group">
          <h3>${title}</h3>
          <p class="muted">${hint}</p>
          ${items.map((item) => `
            <label class="scope-row">
              <input type="checkbox" data-scope="${item.scope}" ${item.checked ? "checked" : ""}>
              <span><strong>${item.label}</strong><br><span class="muted">${item.scope}</span></span>
            </label>
          `).join("")}
        </div>
      `;
    }

    function showLocalFileActions(message) {
      document.getElementById("local-file-actions").style.display = "block";
      document.getElementById("register-status").textContent = message;
    }

    function hideLocalFileActions() {
      document.getElementById("local-file-actions").style.display = "none";
      document.getElementById("register-status").textContent = "This local file is only loaded in the browser until you register it with this local service.";
    }

    function upsertStoredProfileOption(profile) {
      const select = document.getElementById("stored-profile-select");
      let option = Array.from(select.options).find((item) => item.value === profile.profile_id);
      if (!option) {
        option = document.createElement("option");
        option.value = profile.profile_id;
        select.appendChild(option);
      }
      option.textContent = `${profile.display_name} (${profile.profile_id})`;
    }

    async function loadPendingRequests(profileId) {
      const container = document.getElementById("pending-requests");
      const response = await fetch(`/lens/profiles/${encodeURIComponent(profileId)}/pending-requests`);
      if (!response.ok) {
        container.innerHTML = "<p class='muted'>This profile is not registered in the local service yet, so there are no service-side requests to review.</p>";
        return;
      }
      const body = await response.json();
      if (!body.requests.length) {
        container.innerHTML = "<p class='muted'>No pending site requests for this profile yet.</p>";
        return;
      }
      container.innerHTML = body.requests.map((request) => `
        <div class="topic">
          <strong>${request.site_name}</strong><br>
          <span class="muted">${request.purpose}</span><br>
          <a class="button-link" href="/consent/site-access-requests/${encodeURIComponent(request.request_id)}">Review request</a>
        </div>
      `).join("");
    }

    function renderProfile(profile, sourceLabel, options = {}) {
      state.profile = profile;
      document.getElementById("lens-root").style.display = "block";
      document.getElementById("source-status").textContent = sourceLabel;
      if (options.localPreview) {
        showLocalFileActions("This local file is only in your browser. Register it with the local service before using the React demo or reviewing service-side requests.");
      } else {
        hideLocalFileActions();
      }

      const privateTopics = profile.topics.filter((topic) => topic.visibility === "private");
      const publicTopics = computePublicTopics(profile);
      const selectiveTopics = profile.topics.filter((topic) => topic.visibility === "selective");

      document.getElementById("profile-summary").textContent = JSON.stringify({
        display_name: profile.display_name,
        profile_id: profile.profile_id,
        public_topics: publicTopics.length,
        selective_topics: selectiveTopics.length,
        private_topics: privateTopics.length,
        opted_out_topics: profile.opt_out_topics,
        consent: profile.consent
      }, null, 2);

      renderTopicList("private-topics", privateTopics, () => "Only stays local. Never shared through public or site-scoped preview.");
      renderTopicList("public-topics", publicTopics, () => profile.consent.share_public_topics
        ? "Already public because public-topic sharing is enabled."
        : "Hidden because public-topic sharing is disabled.");

      const scopeOptions = [
        { scope: "profile.read", label: "Basic profile identity", checked: true },
        { scope: "topics.public", label: "Public topics", checked: true },
        { scope: "consent.summary", label: "Consent summary", checked: false },
        ...selectiveTopics.map((topic) => ({
          scope: `topics.selective:${topic.topic}`,
          label: `Selective topic: ${humanizeTopic(topic.topic)}`,
          checked: false
        }))
      ];
      const alreadyPublic = scopeOptions.filter((item) => !item.scope.startsWith("topics.selective:"));
      const newlyShared = scopeOptions.filter((item) => item.scope.startsWith("topics.selective:"));
      const scopeList = document.getElementById("scope-list");
      scopeList.innerHTML = [
        renderScopeGroup(
          "Already public or identity-level",
          "These items confirm data that is already public or let the site recognize this portable profile.",
          alreadyPublic
        ),
        renderScopeGroup(
          "Newly shared with this site only",
          "These selective topics are not public. They only appear if you keep them checked.",
          newlyShared
        )
      ].join("");
      scopeList.querySelectorAll("input[type='checkbox']").forEach((node) => node.addEventListener("change", renderSitePreview));
      renderSitePreview();
      loadPendingRequests(profile.profile_id);
    }

    async function loadStoredProfiles() {
      const response = await fetch("/lens/profiles");
      if (!response.ok) {
        return;
      }
      const body = await response.json();
      const select = document.getElementById("stored-profile-select");
      body.profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.profile_id;
        option.textContent = `${profile.display_name} (${profile.profile_id})`;
        select.appendChild(option);
      });
      if (initialProfileId) {
        select.value = initialProfileId;
        if (select.value === initialProfileId) {
          loadStoredProfile(initialProfileId);
        }
      }
    }

    async function loadStoredProfile(profileId) {
      if (!profileId) {
        document.getElementById("source-status").textContent = "Choose a stored profile first.";
        return;
      }
      const response = await fetch(`/lens/profiles/${encodeURIComponent(profileId)}`);
      if (!response.ok) {
        document.getElementById("source-status").textContent = "Could not load that stored profile.";
        return;
      }
      const body = await response.json();
      state.localProfileDocument = null;
      renderProfile(body.profile, "Loaded the locally stored copy from this service.");
    }

    document.getElementById("load-stored-button").addEventListener("click", async () => {
      const profileId = document.getElementById("stored-profile-select").value;
      loadStoredProfile(profileId);
    });

    document.getElementById("profile-file").addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }
      const text = await file.text();
      const profile = JSON.parse(text);
      state.localProfileDocument = profile;
      renderProfile(profile, `Loaded local file: ${file.name}`, { localPreview: true });
    });

    document.getElementById("register-local-profile").addEventListener("click", async () => {
      if (!state.localProfileDocument) {
        showLocalFileActions("Choose a local .orf file first.");
        return;
      }
      const button = document.getElementById("register-local-profile");
      button.disabled = true;
      showLocalFileActions("Registering this profile with the local service…");
      try {
        const response = await fetch("/lens/profiles/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: state.localProfileDocument })
        });
        const body = await response.json();
        if (!response.ok) {
          showLocalFileActions(body.detail || "Could not register this profile with the local service.");
          return;
        }
        state.localProfileDocument = null;
        upsertStoredProfileOption(body.profile);
        document.getElementById("stored-profile-select").value = body.profile.profile_id;
        renderProfile(body.profile, "Registered this local profile with the local service.");
      } catch (error) {
        showLocalFileActions(`Could not register this profile: ${error}`);
      } finally {
        button.disabled = false;
      }
    });

    loadStoredProfiles();
  </script>
</body>
</html>""".replace("NAV_PLACEHOLDER", _render_trust_nav(active="lens"))


def create_app(
    db_path: str | Path | None = None,
    *,
    admin_token: str | None = None,
    rate_limit_window_seconds: int | None = None,
    rate_limit_max_requests: int | None = None,
    pilot_sites_path: str | Path | None = None,
    pilot_sites: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    sync_token: str | None = None,
) -> FastAPI:
    config = _service_config(
        db_path,
        admin_token=admin_token,
        rate_limit_window_seconds=rate_limit_window_seconds,
        rate_limit_max_requests=rate_limit_max_requests,
        pilot_sites_path=pilot_sites_path,
        sync_token=sync_token,
    )
    if pilot_sites is not None and config.pilot_sites_path is not None:
        raise ValueError("Configure pilot sites with either pilot_sites or pilot_sites_path, not both.")

    resolved_pilot_sites: tuple[dict[str, Any], ...] | None = None
    if pilot_sites is not None:
        resolved_pilot_sites = tuple(dict(site) for site in pilot_sites)
    elif config.pilot_sites_path is not None:
        resolved_pilot_sites = _pilot_sites_from_path(config.pilot_sites_path)

    app = FastAPI(
        title="Open Recommender API",
        version="0.1.0",
        description="Hosted sync and public profile API for portable ORF profiles.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = SQLiteStore(config.db_path, pilot_sites=resolved_pilot_sites)
    rate_limiter = FixedWindowRateLimiter(
        window_seconds=config.rate_limit_window_seconds,
        max_requests=config.rate_limit_max_requests,
    )
    browser_secret = store.browser_secret()
    app.state.store = store
    app.state.config = config

    def enforce_rate_limit(request: Request, bucket: str) -> None:
        client_id = request.client.host if request.client is not None else "unknown"
        rate_limiter.check(bucket, client_id)

    def require_local_browser(request: Request) -> None:
        client_id = request.client.host if request.client is not None else "unknown"
        if client_id not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="Local browser surfaces are only available from localhost.")

    def require_admin_token(header_value: str | None) -> None:
        if config.admin_token is None:
            raise HTTPException(status_code=404, detail="Admin endpoints are disabled.")
        if header_value != config.admin_token:
            raise HTTPException(status_code=403, detail="Admin token is invalid.")

    def require_sync_token(header_value: str | None) -> None:
        if config.sync_token is None:
            return
        if header_value != f"Bearer {config.sync_token}":
            raise HTTPException(
                status_code=401,
                detail="Sync token required. Set Authorization: Bearer <sync-token>.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def issue_consent_csrf_token(request_id: str) -> str:
        return hmac.new(
            browser_secret.encode("utf-8"),
            request_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_consent_csrf_token(request_id: str, token: str | None) -> None:
        expected = issue_consent_csrf_token(request_id)
        if token is None or not hmac.compare_digest(expected, token):
            raise HTTPException(status_code=403, detail="Consent review token is invalid.")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": {
                "db_path": config.db_path,
                "admin_endpoints_enabled": config.admin_token is not None,
                "rate_limit_window_seconds": config.rate_limit_window_seconds,
                "rate_limit_max_requests": config.rate_limit_max_requests,
                "pilot_sites_count": len(store.list_pilot_sites()),
                "pilot_sites_path": config.pilot_sites_path,
                "sync_auth_required": config.sync_token is not None,
            },
        }

    @app.post("/profiles")
    def upsert_profile(body: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = ORFProfile.from_document(body["profile"])
            saved = store.save_profile(profile)
        except (KeyError, TypeError, ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "profile_id": saved.profile_id,
            "public_profile": saved.public_projection(),
        }

    @app.get("/profiles/{profile_id}/public")
    def get_public_profile(profile_id: str) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        return profile.public_projection()

    @app.get("/profiles/{profile_id}/events")
    def get_events(
        profile_id: str,
        after_clock: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_sync_token(authorization)
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        return {
            "profile_id": profile_id,
            "events": store.list_events(profile_id, after_clock=after_clock),
        }

    @app.post("/profiles/{profile_id}/events")
    def post_events(
        profile_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_sync_token(authorization)
        try:
            events = [SignedEvent.from_dict(item) for item in body.get("events", [])]
            profile = store.append_events(profile_id, events)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "profile_id": profile.profile_id,
            "accepted_events": len(events),
            "updated_at": profile.updated_at,
            "public_profile": profile.public_projection(),
        }

    @app.post("/profiles/{profile_id}/challenges")
    def create_challenge(profile_id: str, request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "profile-challenge")
        try:
            challenge = store.create_challenge(profile_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return challenge

    @app.post("/profiles/{profile_id}/challenge-response")
    def verify_challenge(profile_id: str, body: dict[str, Any], request: Request) -> dict[str, bool]:
        enforce_rate_limit(request, "profile-verify")
        try:
            verified = store.verify_challenge_response(
                profile_id,
                str(body["challenge_id"]),
                str(body["signature"]),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"verified": verified}

    @app.post("/profiles/{profile_id}/site-access-requests")
    def create_site_access_request(profile_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "access-request-create")
        try:
            has_explicit_scope_tiers = "required_scopes" in body or "optional_scopes" in body
            if has_explicit_scope_tiers and "requested_scopes" in body:
                raise ValueError(
                    "Use either legacy requested_scopes or required_scopes/optional_scopes, not both."
                )
            access_request = store.create_access_request(
                profile_id,
                site_id=str(body["site_id"]),
                purpose=str(body["purpose"]),
                requested_scopes=(
                    [str(scope) for scope in body.get("requested_scopes", [])]
                    if not has_explicit_scope_tiers
                    else None
                ),
                required_scopes=(
                    [str(scope) for scope in body.get("required_scopes", [])]
                    if has_explicit_scope_tiers
                    else None
                ),
                optional_scopes=(
                    [str(scope) for scope in body.get("optional_scopes", [])]
                    if has_explicit_scope_tiers
                    else None
                ),
                expires_at=str(body["expires_at"]) if body.get("expires_at") is not None else None,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
            "consent_review_url": str(request.url_for("get_consent_review_page", request_id=access_request.request_id)),
        }

    @app.get("/site-access-requests/{request_id}")
    def get_site_access_request(request_id: str, request: Request) -> dict[str, Any]:
        try:
            profile_id, access_request = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
            "consent_review_url": str(request.url_for("get_consent_review_page", request_id=request_id)),
        }

    @app.get("/consent", response_class=HTMLResponse)
    def get_consent_inbox(request: Request, profile_id: str | None = Query(default=None)) -> HTMLResponse:
        require_local_browser(request)
        pending_requests = store.list_access_requests(profile_id=profile_id, status="pending")
        return HTMLResponse(
            _render_consent_index_page(
                pending_requests=pending_requests,
                profile_id=profile_id,
            )
        )

    @app.get("/consent/grants", response_class=HTMLResponse)
    def get_consent_grants_page(request: Request, profile_id: str | None = Query(default=None)) -> HTMLResponse:
        require_local_browser(request)
        grants = store.list_grants(profile_id=profile_id)
        csrf_tokens = {
            str(item["grant"]["grant_id"]): issue_consent_csrf_token(f"grant:{item['grant']['grant_id']}")
            for item in grants
        }
        return HTMLResponse(
            _render_grants_page(
                grants=grants,
                csrf_tokens=csrf_tokens,
                profile_id=profile_id,
            )
        )

    @app.post("/consent/grants/{grant_id}/revoke")
    def revoke_grant_from_browser(
        grant_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
        x_open_recommender_csrf_token: str | None = Header(default=None, alias="X-Open-Recommender-CSRF-Token"),
    ) -> dict[str, Any]:
        require_local_browser(request)
        verify_consent_csrf_token(f"grant:{grant_id}", x_open_recommender_csrf_token)
        enforce_rate_limit(request, "grant-revoke")
        payload = body or {}
        try:
            grant = store.revoke_grant(
                grant_id,
                actor=str(payload.get("actor", "browser-consent-ui")),
                reason=str(payload["reason"]) if payload.get("reason") is not None else None,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "message": "Grant revoked from browser trust app.",
            "grant": grant.to_dict(),
        }

    @app.get("/consent/site-access-requests/{request_id}", response_class=HTMLResponse)
    def get_consent_review_page(request_id: str, request: Request) -> HTMLResponse:
        require_local_browser(request)
        try:
            profile_id, access_request = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        preview_projection = None
        profile = store.get_profile(profile_id)
        if profile is not None:
            preview_projection = profile.consented_projection(
                access_request.requested_scopes,
                site_id=access_request.site_id,
                grant_id="preview",
                schema_version=access_request.schema_version,
            )
        csrf_token = issue_consent_csrf_token(request_id)
        return HTMLResponse(
            _render_consent_review_page(
                request_id=request_id,
                csrf_token=csrf_token,
                access_request=access_request.to_dict(),
                projection_preview=preview_projection,
                profile_id=profile_id,
            )
        )

    @app.get("/consent/site-access-requests/{request_id}/review-data")
    def get_consent_review_data(request_id: str, request: Request) -> dict[str, Any]:
        require_local_browser(request)
        try:
            profile_id, access_request = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        preview_projection = None
        profile = store.get_profile(profile_id)
        if profile is not None:
            preview_projection = profile.consented_projection(
                access_request.requested_scopes,
                site_id=access_request.site_id,
                grant_id="preview",
                schema_version=access_request.schema_version,
            )
        return {
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
            "projection_preview": preview_projection,
            "csrf_token": issue_consent_csrf_token(request_id),
            "scope_groups": _scope_group_payload(
                list(access_request.required_scopes),
                list(access_request.optional_scopes),
            ),
        }

    @app.post("/consent/site-access-requests/{request_id}/approve")
    def approve_site_access_request_browser(
        request_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
        x_open_recommender_csrf_token: str | None = Header(default=None, alias="X-Open-Recommender-CSRF-Token"),
    ) -> dict[str, Any]:
        require_local_browser(request)
        verify_consent_csrf_token(request_id, x_open_recommender_csrf_token)
        enforce_rate_limit(request, "access-request-approve")
        payload = body or {}
        try:
            access_request, grant = store.approve_access_request(
                request_id,
                approved_scopes=[str(scope) for scope in payload.get("approved_scopes", [])]
                if payload.get("approved_scopes") is not None
                else None,
                grant_expires_at=(
                    str(payload["grant_expires_at"])
                    if payload.get("grant_expires_at") is not None
                    else None
                ),
                actor=str(payload.get("actor", "browser-consent-ui")),
            )
            profile_id, _ = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "message": "Request approved from browser consent UI.",
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
            "grant": grant.to_dict(),
        }

    @app.post("/consent/site-access-requests/{request_id}/deny")
    def deny_site_access_request_browser(
        request_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
        x_open_recommender_csrf_token: str | None = Header(default=None, alias="X-Open-Recommender-CSRF-Token"),
    ) -> dict[str, Any]:
        require_local_browser(request)
        verify_consent_csrf_token(request_id, x_open_recommender_csrf_token)
        enforce_rate_limit(request, "access-request-deny")
        payload = body or {}
        try:
            access_request = store.deny_access_request(
                request_id,
                reason=str(payload["reason"]) if payload.get("reason") is not None else None,
                actor=str(payload.get("actor", "browser-consent-ui")),
            )
            profile_id, _ = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "message": "Request denied from browser consent UI.",
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
        }

    @app.get("/lens", response_class=HTMLResponse)
    def get_profile_lens(request: Request) -> HTMLResponse:
        require_local_browser(request)
        return HTMLResponse(_render_profile_lens_page())

    @app.get("/lens/profiles")
    def list_profile_lens_profiles(request: Request) -> dict[str, Any]:
        require_local_browser(request)
        profiles = [
            {
                "profile_id": profile.profile_id,
                "display_name": profile.display_name,
                "updated_at": profile.updated_at,
            }
            for profile in store.list_profiles()
        ]
        return {"profiles": profiles}

    @app.post("/lens/profiles/import")
    def import_profile_lens_profile(body: dict[str, Any], request: Request) -> dict[str, Any]:
        require_local_browser(request)
        try:
            profile = ORFProfile.from_document(body["profile"])
            saved = store.save_profile(profile)
        except (KeyError, TypeError, ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "message": "Profile registered in the local service.",
            "profile": _lens_profile_payload(saved),
        }

    @app.get("/lens/profiles/{profile_id}")
    def get_profile_lens_profile(profile_id: str, request: Request) -> dict[str, Any]:
        require_local_browser(request)
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        return {"profile": _lens_profile_payload(profile)}

    @app.get("/lens/profiles/{profile_id}/pending-requests")
    def get_profile_lens_pending_requests(profile_id: str, request: Request) -> dict[str, Any]:
        require_local_browser(request)
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        requests = [
            {
                "request_id": item["access_request"]["request_id"],
                "site_id": item["access_request"]["site_id"],
                "site_name": item["access_request"]["site_name"],
                "purpose": item["access_request"]["purpose"],
                "created_at": item["access_request"]["created_at"],
            }
            for item in store.list_access_requests(profile_id=profile_id, status="pending")
        ]
        return {"profile_id": profile_id, "requests": requests}

    @app.post("/site-access-requests/{request_id}/approve")
    def approve_site_access_request(
        request_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        enforce_rate_limit(request, "access-request-approve")
        payload = body or {}
        try:
            access_request, grant = store.approve_access_request(
                request_id,
                approved_scopes=[str(scope) for scope in payload.get("approved_scopes", [])]
                if payload.get("approved_scopes") is not None
                else None,
                grant_expires_at=(
                    str(payload["grant_expires_at"])
                    if payload.get("grant_expires_at") is not None
                    else None
                ),
                actor=str(payload.get("actor", "cli")),
            )
            profile_id, _ = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
            "grant": grant.to_dict(),
        }

    @app.post("/site-access-requests/{request_id}/deny")
    def deny_site_access_request(
        request_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        enforce_rate_limit(request, "access-request-deny")
        payload = body or {}
        try:
            access_request = store.deny_access_request(
                request_id,
                reason=str(payload["reason"]) if payload.get("reason") is not None else None,
                actor=str(payload.get("actor", "cli")),
            )
            profile_id, _ = store.get_access_request(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "profile_id": profile_id,
            "access_request": access_request.to_dict(),
        }

    @app.post("/site-access-requests/{request_id}/exchange")
    def begin_site_access_exchange(request_id: str, request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "grant-exchange")
        try:
            access_request, grant, challenge = store.begin_grant_exchange(request_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "access_request": access_request.to_dict(),
            "grant": grant.to_dict(),
            "challenge": challenge,
            "challenge_payload": challenge,
        }

    @app.post("/site-access-requests/{request_id}/verify")
    def verify_site_access_exchange(request_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "grant-verify")
        try:
            grant, session = store.exchange_grant_session(
                request_id,
                challenge_id=str(body["challenge_id"]),
                signature=str(body["signature"]),
                session_expires_at=(
                    str(body["session_expires_at"])
                    if body.get("session_expires_at") is not None
                    else None
                ),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "verified": True,
            "grant": grant.to_dict(),
            "session": session.to_dict(),
        }

    @app.get("/grant-sessions/{session_id}/projection")
    def get_grant_session_projection(session_id: str, request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "projection-read")
        try:
            session = store.get_grant_session(session_id)
            projection = store.get_consented_projection(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "session": session.to_dict(),
            "projection": projection,
        }

    @app.get("/demo/site/{profile_id}")
    def get_demo_site(profile_id: str) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        return _build_demo_response(profile, verified=False)

    @app.post("/demo/site/{profile_id}/challenge")
    def create_demo_challenge(profile_id: str, request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "demo-challenge")
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        challenge = store.create_challenge(profile_id)
        return {
            **_build_demo_response(profile, verified=False),
            "challenge": challenge,
            "challenge_payload": challenge,
            "instructions": "Sign challenge_payload with the ORF private key, then POST the signature to /demo/site/{profile_id}/verify.",
        }

    @app.post("/demo/site/{profile_id}/verify")
    def verify_demo_challenge(profile_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        enforce_rate_limit(request, "demo-verify")
        try:
            store.verify_challenge_response(
                profile_id,
                str(body["challenge_id"]),
                str(body["signature"]),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, InvalidSignature) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        return _build_demo_response(profile, verified=True)

    @app.get("/admin/pilot-sites")
    def get_pilot_sites(
        x_open_recommender_admin_token: str | None = Header(
            default=None, alias="X-Open-Recommender-Admin-Token"
        ),
    ) -> dict[str, Any]:
        require_admin_token(x_open_recommender_admin_token)
        return {"sites": store.list_pilot_sites()}

    @app.get("/admin/audit-events")
    def get_audit_events(
        limit: int = Query(default=100, ge=1, le=500),
        event_type: str | None = None,
        site_id: str | None = None,
        profile_id: str | None = None,
        request_id: str | None = None,
        grant_id: str | None = None,
        session_id: str | None = None,
        x_open_recommender_admin_token: str | None = Header(
            default=None, alias="X-Open-Recommender-Admin-Token"
        ),
    ) -> dict[str, Any]:
        require_admin_token(x_open_recommender_admin_token)
        return {
            "events": store.list_audit_events(
                limit=limit,
                event_type=event_type,
                site_id=site_id,
                profile_id=profile_id,
                request_id=request_id,
                grant_id=grant_id,
                session_id=session_id,
            )
        }

    return app
