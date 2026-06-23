from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .models import CURRENT_CONTRACT_SCHEMA_VERSION


JsonSender = Callable[[str, str, dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class PartnerSDKError(Exception):
    message: str
    status_code: int | None = None
    detail: Any = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status={self.status_code})"


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
    try:
        with request.urlopen(req) as response:
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


def _default_send_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _http_send(method, url, body)


class PartnerClient:
    """Thin site-side wrapper for the pilot access flow."""

    def __init__(
        self,
        base_url: str,
        *,
        sync_token: str | None = None,
        send_json: JsonSender | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sync_token = sync_token
        self._send_json = send_json or _default_send_json

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._send_json(method, f"{self.base_url}{path}", body)

    def _sync_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Request that includes the sync-tier Bearer token when configured."""
        extra: dict[str, str] = {}
        if self.sync_token is not None:
            extra["Authorization"] = f"Bearer {self.sync_token}"
        return _http_send(method, f"{self.base_url}{path}", body, extra_headers=extra or None)

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
        return self._request(
            "POST",
            f"/profiles/{profile_id}/site-access-requests",
            payload,
        )

    def get_access_request(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/site-access-requests/{request_id}")

    def exchange_access_request(self, request_id: str) -> dict[str, Any]:
        return self._request("POST", f"/site-access-requests/{request_id}/exchange")

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
        return self._request(
            "POST",
            f"/site-access-requests/{request_id}/verify",
            payload,
        )

    def get_projection(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/grant-sessions/{session_id}/projection")

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
        return self._request("POST", f"/grant-sessions/{session_id}/rank", payload)

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
        return self._request("POST", f"/grant-sessions/{session_id}/rank/feedback", payload)

    def push_events(self, profile_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Push signed events to the hosted sync store.

        Requires a sync token when the service is configured with
        ``OPEN_RECOMMENDER_SYNC_TOKEN`` (paid tier).
        """
        return self._sync_request(
            "POST",
            f"/profiles/{profile_id}/events",
            {"events": events},
        )

    def pull_events(self, profile_id: str, *, after_clock: int = 0) -> dict[str, Any]:
        """Pull signed events from the hosted sync store since *after_clock*.

        Requires a sync token when the service is configured with
        ``OPEN_RECOMMENDER_SYNC_TOKEN`` (paid tier).
        """
        path = f"/profiles/{profile_id}/events"
        if after_clock:
            path += f"?after_clock={after_clock}"
        return self._sync_request("GET", path)
