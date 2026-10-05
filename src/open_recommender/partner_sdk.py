from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .models import CURRENT_CONTRACT_SCHEMA_VERSION


JsonSender = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class PartnerSDKError(Exception):
    message: str
    status_code: int | None = None
    detail: Any = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status={self.status_code})"


class _NoCredentialRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward backend credentials to a redirect target.


def _http_send(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, method=method, headers=headers)
    opener = request.build_opener(_NoCredentialRedirect()).open if extra_headers else request.urlopen
    try:
        with opener(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as http_error:
        try:
            payload = json.loads(http_error.read().decode("utf-8"))
        except Exception:
            payload = {"detail": http_error.reason}
        raise PartnerSDKError(
            message=f"ORF API call failed for {method} {url}",
            status_code=http_error.code,
            detail=payload.get("detail"),
        ) from http_error
    except error.URLError as network_error:
        raise PartnerSDKError(
            message=f"Unable to reach ORF service at {url}",
            detail=str(network_error.reason),
        ) from network_error


class PartnerClient:
    """Thin site-side wrapper for the pilot access flow."""

    def __init__(
        self,
        base_url: str,
        *,
        sync_token: str | None = None,
        site_id: str | None = None,
        site_token: str | None = None,
        send_json: JsonSender | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sync_token = sync_token
        if (site_id is None) != (site_token is None) or site_id == "" or site_token == "":
            raise ValueError("Configure both site_id and site_token, or neither.")
        self.site_id = site_id
        self.site_token = site_token
        self._send_json = send_json or _http_send

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
        *, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        if extra_headers:
            return self._send_json(method, f"{self.base_url}{path}", body, extra_headers=extra_headers)
        return self._send_json(method, f"{self.base_url}{path}", body)

    def _site_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = None if self.site_token is None else {
            "X-ORF-Site-ID": self.site_id, "X-ORF-Site-Token": self.site_token,
        }
        return self._request(method, path, body, extra_headers=headers)

    def _sync_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Request that includes the sync-tier Bearer token when configured."""
        if self.sync_token is None:
            return self._request(method, path, body)
        return self._request(method, path, body,
            extra_headers={"Authorization": f"Bearer {self.sync_token}"})

    def create_access_request(
        self,
        *,
        profile_id: str,
        site_id: str,
        purpose: str,
        requested_scopes: list[str] | None = None,
        required_scopes: list[str] | None = None,
        optional_scopes: list[str] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"site_id": site_id, "purpose": purpose}
        has_explicit_scope_tiers = required_scopes is not None or optional_scopes is not None
        if has_explicit_scope_tiers and requested_scopes is not None:
            raise ValueError(
                "Use either requested_scopes or required_scopes/optional_scopes, not both."
            )
        if has_explicit_scope_tiers:
            if required_scopes is not None:
                payload["required_scopes"] = required_scopes
            if optional_scopes is not None:
                payload["optional_scopes"] = optional_scopes
        else:
            payload["requested_scopes"] = requested_scopes or []
        if expires_at is not None:
            payload["expires_at"] = expires_at
        return self._site_request(
            "POST",
            f"/profiles/{profile_id}/site-access-requests",
            payload,
        )

    def get_access_request(self, request_id: str) -> dict[str, Any]:
        return self._site_request("GET", f"/site-access-requests/{request_id}")

    def exchange_access_request(self, request_id: str) -> dict[str, Any]:
        return self._site_request("POST", f"/site-access-requests/{request_id}/exchange")

    def verify_access_request(
        self,
        *,
        request_id: str,
        challenge_id: str,
        signature: str,
        session_expires_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "challenge_id": challenge_id,
            "signature": signature,
        }
        if session_expires_at is not None:
            payload["session_expires_at"] = session_expires_at
        return self._site_request(
            "POST",
            f"/site-access-requests/{request_id}/verify",
            payload,
        )

    def get_projection(self, session_id: str) -> dict[str, Any]:
        return self._site_request("GET", f"/grant-sessions/{session_id}/projection")

    def rank_candidates(
        self,
        session_id: str,
        *,
        candidates: list[dict[str, Any]],
        top_n: int | None = None,
        include_debug: bool = False,
        schema_version: str = CURRENT_CONTRACT_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        """Rerank site candidates inside an approved grant-session boundary."""
        payload: dict[str, Any] = {
            "schema_version": schema_version,
            "include_debug": include_debug,
            "candidates": candidates,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        return self._site_request("POST", f"/grant-sessions/{session_id}/rank", payload)

    def record_ranking_feedback(
        self,
        session_id: str,
        *,
        events: list[dict[str, Any]],
        schema_version: str = CURRENT_CONTRACT_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        """Record site-local ranking outcomes for future reranks on the same grant."""
        payload: dict[str, Any] = {
            "schema_version": schema_version,
            "events": events,
        }
        return self._site_request("POST", f"/grant-sessions/{session_id}/rank/feedback", payload)

    def push_events(self, profile_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Push signed events to the hosted sync store.

        Requires a sync token when the service is configured with
        ``OPEN_RECOMMENDER_SYNC_TOKEN``. Events themselves must be owner-signed.
        """
        return self._sync_request(
            "POST",
            f"/profiles/{profile_id}/events",
            {"events": events},
        )

    def pull_events(
        self, profile_id: str, *, owner_proof: dict[str, str], after_clock: int = 0
    ) -> dict[str, Any]:
        """Owner-side only: read raw history with a one-time sync-read proof.

        Proof must bind this profile and exact after_clock. An optional shared
        sync token is an additional gate, never a substitute for owner control.
        """
        return self._sync_request("POST", f"/profiles/{profile_id}/events/read",
            {"after_clock": after_clock, "owner_proof": owner_proof})
