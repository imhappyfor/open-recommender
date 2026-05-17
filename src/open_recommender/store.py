from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature

from .crypto import verify_signature
from .models import (
    AccessGrant,
    AccessRequestStatus,
    ConsentSettings,
    GrantSession,
    ORFProfile,
    SignedEvent,
    SiteAccessRequest,
    SyncState,
    normalize_scope_set,
    utc_now,
)


STORE_SCHEMA_VERSION = 3
CHALLENGE_TTL_SECONDS = 300
DEFAULT_GRANT_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_GRANT_SESSION_TTL_SECONDS = 30 * 60
DEFAULT_PILOT_SITES = (
    {
        "site_id": "open-news-demo",
        "site_name": "Open News Demo",
        "allowed_scopes": ["profile.read", "topics.public", "consent.summary"],
        "allow_selective_topics": True,
    },
)


class SQLiteStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        pilot_sites: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.pilot_sites = tuple(pilot_sites or DEFAULT_PILOT_SITES)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                """
            )
            row = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            version = int(row["version"]) if row is not None else 0
            if version > STORE_SCHEMA_VERSION:
                raise ValueError("Store schema is newer than this service supports.")
            if version < 1:
                self._migrate_v1(connection)
                version = 1
            if version < 2:
                self._migrate_v2(connection)
                version = 2
            if version < 3:
                self._migrate_v3(connection)
                version = 3
            self._set_schema_version(connection, version)
            self._seed_pilot_sites(connection)

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                profile_id TEXT PRIMARY KEY,
                public_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                clock INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_json TEXT NOT NULL,
                UNIQUE (profile_id, event_id)
            );

            CREATE TABLE IF NOT EXISTS challenges (
                challenge_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._set_schema_version(connection, 1)

    def _migrate_v2(self, connection: sqlite3.Connection) -> None:
        challenge_columns = self._table_columns(connection, "challenges")
        if "request_id" not in challenge_columns:
            connection.execute("ALTER TABLE challenges ADD COLUMN request_id TEXT")
        if "site_id" not in challenge_columns:
            connection.execute("ALTER TABLE challenges ADD COLUMN site_id TEXT")
        if "grant_id" not in challenge_columns:
            connection.execute("ALTER TABLE challenges ADD COLUMN grant_id TEXT")
        if "challenge_type" not in challenge_columns:
            connection.execute(
                "ALTER TABLE challenges ADD COLUMN challenge_type TEXT NOT NULL DEFAULT 'profile'"
            )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                site_id TEXT PRIMARY KEY,
                site_name TEXT NOT NULL,
                status TEXT NOT NULL,
                allowed_scopes_json TEXT NOT NULL,
                allow_selective_topics INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_requests (
                request_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS grants (
                grant_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                grant_json TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS grant_sessions (
                session_id TEXT PRIMARY KEY,
                grant_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                session_json TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                profile_id TEXT,
                site_id TEXT,
                request_id TEXT,
                grant_id TEXT,
                session_id TEXT,
                challenge_id TEXT,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._set_schema_version(connection, 2)

    def _migrate_v3(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS service_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._set_schema_version(connection, 3)

    def _table_columns(self, connection: sqlite3.Connection, table_name: str) -> set[str]:
        return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}

    def _set_schema_version(self, connection: sqlite3.Connection, version: int) -> None:
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    def _seed_pilot_sites(self, connection: sqlite3.Connection) -> None:
        now = utc_now()
        for site in self.pilot_sites:
            allowed_scopes, _ = normalize_scope_set(site.get("allowed_scopes", []))
            connection.execute(
                """
                INSERT INTO sites (
                    site_id, site_name, status, allowed_scopes_json, allow_selective_topics,
                    config_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id) DO UPDATE SET
                    site_name = excluded.site_name,
                    status = excluded.status,
                    allowed_scopes_json = excluded.allowed_scopes_json,
                    allow_selective_topics = excluded.allow_selective_topics,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(site["site_id"]),
                    str(site["site_name"]),
                    str(site.get("status", "active")),
                    json.dumps(list(allowed_scopes), sort_keys=True),
                    1 if bool(site.get("allow_selective_topics")) else 0,
                    json.dumps(dict(site), sort_keys=True),
                    now,
                    now,
                ),
            )

    def _parse_timestamp(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _is_expired(self, value: str | None) -> bool:
        if value is None:
            return False
        return self._parse_timestamp(value) <= datetime.now(timezone.utc)

    def _future_timestamp(self, seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(
            microsecond=0
        ).isoformat()

    def _challenge_expired(self, created_at: str) -> bool:
        age = datetime.now(timezone.utc) - self._parse_timestamp(created_at)
        return age.total_seconds() > CHALLENGE_TTL_SECONDS

    def _site_record(self, site_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT site_id, site_name, status, allowed_scopes_json, allow_selective_topics, config_json
                FROM sites
                WHERE site_id = ?
                """,
                (site_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Unknown pilot site.")
        allowed_scopes = tuple(json.loads(row["allowed_scopes_json"]))
        return {
            "site_id": row["site_id"],
            "site_name": row["site_name"],
            "status": row["status"],
            "allowed_scopes": allowed_scopes,
            "allow_selective_topics": bool(row["allow_selective_topics"]),
            "config": json.loads(row["config_json"]),
        }

    def list_pilot_sites(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT site_id, site_name, status, allowed_scopes_json, allow_selective_topics
                FROM sites
                ORDER BY site_id ASC
                """
            ).fetchall()
        return [
            {
                "site_id": row["site_id"],
                "site_name": row["site_name"],
                "status": row["status"],
                "allowed_scopes": json.loads(row["allowed_scopes_json"]),
                "allow_selective_topics": bool(row["allow_selective_topics"]),
            }
            for row in rows
        ]

    def list_audit_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        site_id: str | None = None,
        profile_id: str | None = None,
        request_id: str | None = None,
        grant_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if site_id is not None:
            clauses.append("site_id = ?")
            params.append(site_id)
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if request_id is not None:
            clauses.append("request_id = ?")
            params.append(request_id)
        if grant_id is not None:
            clauses.append("grant_id = ?")
            params.append(grant_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)

        query = """
            SELECT audit_id, created_at, event_json
            FROM audit_events
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, audit_id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["event_json"])
            payload["audit_id"] = row["audit_id"]
            payload["recorded_at"] = row["created_at"]
            events.append(payload)
        return events

    def browser_secret(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_value FROM service_state WHERE state_key = ?",
                ("browser_secret",),
            ).fetchone()
            if row is not None:
                return str(row["state_value"])

            secret = secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO service_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                """,
                ("browser_secret", secret, utc_now()),
            )
        return secret

    def _validate_site_scopes(self, site: dict[str, Any], scopes: tuple[str, ...]) -> None:
        allowed_scopes = set(site["allowed_scopes"])
        for scope in scopes:
            if scope in allowed_scopes:
                continue
            if site["allow_selective_topics"] and scope.startswith("topics.selective:"):
                continue
            raise ValueError(f"Scope '{scope}' is not allowed for pilot site '{site['site_id']}'.")

    def _write_audit_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        *,
        profile_id: str | None = None,
        site_id: str | None = None,
        request_id: str | None = None,
        grant_id: str | None = None,
        session_id: str | None = None,
        challenge_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        created_at = utc_now()
        event_payload = {
            "event_type": event_type,
            "profile_id": profile_id,
            "site_id": site_id,
            "request_id": request_id,
            "grant_id": grant_id,
            "session_id": session_id,
            "challenge_id": challenge_id,
            "created_at": created_at,
            **(payload or {}),
        }
        connection.execute(
            """
            INSERT INTO audit_events (
                audit_id, event_type, profile_id, site_id, request_id, grant_id,
                session_id, challenge_id, event_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                secrets.token_urlsafe(12),
                event_type,
                profile_id,
                site_id,
                request_id,
                grant_id,
                session_id,
                challenge_id,
                json.dumps(event_payload, sort_keys=True),
                created_at,
            ),
        )

    def create_access_request(
        self,
        profile_id: str,
        *,
        site_id: str,
        purpose: str,
        requested_scopes: list[str] | tuple[str, ...],
        expires_at: str | None = None,
    ) -> SiteAccessRequest:
        if self.get_profile(profile_id) is None:
            raise KeyError(f"Unknown profile: {profile_id}")
        site = self._site_record(site_id)
        if site["status"] != "active":
            raise ValueError("Pilot site is not active.")

        normalized_scopes, ignored_scopes = normalize_scope_set(requested_scopes)
        if not normalized_scopes:
            raise ValueError("At least one known scope is required.")
        self._validate_site_scopes(site, normalized_scopes)

        request = SiteAccessRequest.create(
            site_id=site["site_id"],
            site_name=site["site_name"],
            purpose=purpose,
            requested_scopes=normalized_scopes,
            expires_at=expires_at,
        )
        if ignored_scopes:
            request.extra_fields["ignored_requested_scopes"] = list(ignored_scopes)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO access_requests (
                    request_id, profile_id, site_id, status, request_json, created_at, expires_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    profile_id,
                    site["site_id"],
                    request.status.value,
                    json.dumps(request.to_dict(), sort_keys=True),
                    request.created_at,
                    request.expires_at,
                    request.created_at,
                ),
            )
            self._write_audit_event(
                connection,
                "access-request.created",
                profile_id=profile_id,
                site_id=site["site_id"],
                request_id=request.request_id,
                payload={"requested_scopes": list(request.requested_scopes), "purpose": purpose},
            )
        return request

    def get_access_request(self, request_id: str) -> tuple[str, SiteAccessRequest]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT profile_id, request_json, status, expires_at
                FROM access_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Unknown access request.")

            request = SiteAccessRequest.from_dict(json.loads(row["request_json"]))
            if request.status == AccessRequestStatus.PENDING and self._is_expired(row["expires_at"]):
                request.status = AccessRequestStatus.EXPIRED
                connection.execute(
                    """
                    UPDATE access_requests
                    SET status = ?, request_json = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (
                        request.status.value,
                        json.dumps(request.to_dict(), sort_keys=True),
                        utc_now(),
                        request_id,
                    ),
                )
            return str(row["profile_id"]), request

    def list_access_requests(
        self,
        *,
        profile_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        query = """
            SELECT request_id, profile_id
            FROM access_requests
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, request_id DESC"

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        requests: list[dict[str, Any]] = []
        for row in rows:
            request_profile_id, access_request = self.get_access_request(str(row["request_id"]))
            if status is not None and access_request.status.value != status:
                continue
            requests.append(
                {
                    "profile_id": request_profile_id,
                    "access_request": access_request.to_dict(),
                }
            )
        return requests

    def list_grants(self, *, profile_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if profile_id is not None:
            clauses.append("g.profile_id = ?")
            params.append(profile_id)

        query = """
            SELECT
                g.grant_id,
                g.request_id,
                g.profile_id,
                g.site_id,
                g.grant_json,
                g.issued_at,
                g.expires_at,
                s.site_name
            FROM grants g
            LEFT JOIN sites s ON s.site_id = g.site_id
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY g.issued_at DESC, g.grant_id DESC"

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        grants: list[dict[str, Any]] = []
        for row in rows:
            grant = AccessGrant.from_dict(json.loads(row["grant_json"]))
            revoked_at = grant.extra_fields.get("revoked_at")
            status = "active"
            if revoked_at is not None:
                status = "revoked"
            elif self._is_expired(grant.expires_at):
                status = "expired"
            grants.append(
                {
                    "grant": grant.to_dict(),
                    "status": status,
                    "site_name": row["site_name"] or grant.site_id,
                    "profile_id": row["profile_id"],
                }
            )
        return grants

    def revoke_grant(
        self,
        grant_id: str,
        *,
        actor: str = "cli",
        reason: str | None = None,
    ) -> AccessGrant:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT grant_id, request_id, profile_id, site_id, grant_json
                FROM grants
                WHERE grant_id = ?
                """,
                (grant_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Unknown grant.")

            grant = AccessGrant.from_dict(json.loads(row["grant_json"]))
            if grant.extra_fields.get("revoked_at") is not None:
                raise ValueError("Grant has already been revoked.")

            revoked_at = utc_now()
            grant.expires_at = revoked_at
            grant.extra_fields["revoked_at"] = revoked_at
            grant.extra_fields["revoked_by"] = actor
            if reason is not None:
                grant.extra_fields["revocation_reason"] = reason

            connection.execute(
                """
                UPDATE grants
                SET grant_json = ?, expires_at = ?
                WHERE grant_id = ?
                """,
                (
                    json.dumps(grant.to_dict(), sort_keys=True),
                    grant.expires_at,
                    grant.grant_id,
                ),
            )
            self._write_audit_event(
                connection,
                "grant.revoked",
                profile_id=str(row["profile_id"]),
                site_id=str(row["site_id"]),
                request_id=str(row["request_id"]),
                grant_id=grant.grant_id,
                payload={"actor": actor, "reason": reason, "revoked_at": revoked_at},
            )
        return grant

    def get_access_grant(self, request_id: str) -> AccessGrant:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT grant_json FROM grants WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise KeyError("No grant for request.")
        grant = AccessGrant.from_dict(json.loads(row["grant_json"]))
        if grant.extra_fields.get("revoked_at") is not None:
            raise ValueError("Grant has been revoked.")
        if self._is_expired(grant.expires_at):
            raise ValueError("Grant has expired.")
        return grant

    def approve_access_request(
        self,
        request_id: str,
        *,
        approved_scopes: list[str] | tuple[str, ...] | None = None,
        grant_expires_at: str | None = None,
        actor: str = "cli",
    ) -> tuple[SiteAccessRequest, AccessGrant]:
        profile_id, request = self.get_access_request(request_id)
        if request.status != AccessRequestStatus.PENDING:
            raise ValueError("Only pending requests can be approved.")

        site = self._site_record(request.site_id)
        requested_scope_set = set(request.requested_scopes)
        normalized_scopes, ignored_scopes = normalize_scope_set(approved_scopes or request.requested_scopes)
        if not normalized_scopes:
            raise ValueError("At least one approved scope is required.")
        if not set(normalized_scopes).issubset(requested_scope_set):
            raise ValueError("Approved scopes must be a subset of the request scopes.")
        self._validate_site_scopes(site, normalized_scopes)

        grant = AccessGrant.create(
            request_id=request.request_id,
            profile_id=profile_id,
            site_id=request.site_id,
            approved_scopes=normalized_scopes,
            expires_at=grant_expires_at or self._future_timestamp(DEFAULT_GRANT_TTL_SECONDS),
        )
        grant.extra_fields["exchange_method"] = "challenge"
        if ignored_scopes:
            grant.extra_fields["ignored_approved_scopes"] = list(ignored_scopes)

        request.status = AccessRequestStatus.APPROVED
        request.extra_fields["grant_id"] = grant.grant_id
        request.extra_fields["approved_scopes"] = list(grant.approved_scopes)
        request.extra_fields["decided_at"] = grant.issued_at
        request.extra_fields["decision_actor"] = actor

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE access_requests
                SET status = ?, request_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    request.status.value,
                    json.dumps(request.to_dict(), sort_keys=True),
                    utc_now(),
                    request.request_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO grants (grant_id, request_id, profile_id, site_id, grant_json, issued_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.request_id,
                    grant.profile_id,
                    grant.site_id,
                    json.dumps(grant.to_dict(), sort_keys=True),
                    grant.issued_at,
                    grant.expires_at,
                ),
            )
            self._write_audit_event(
                connection,
                "access-request.approved",
                profile_id=profile_id,
                site_id=request.site_id,
                request_id=request.request_id,
                grant_id=grant.grant_id,
                payload={"approved_scopes": list(grant.approved_scopes), "actor": actor},
            )
        return request, grant

    def deny_access_request(
        self, request_id: str, *, reason: str | None = None, actor: str = "cli"
    ) -> SiteAccessRequest:
        profile_id, request = self.get_access_request(request_id)
        if request.status != AccessRequestStatus.PENDING:
            raise ValueError("Only pending requests can be denied.")

        request.status = AccessRequestStatus.DENIED
        request.extra_fields["decided_at"] = utc_now()
        request.extra_fields["decision_actor"] = actor
        if reason:
            request.extra_fields["denial_reason"] = reason

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE access_requests
                SET status = ?, request_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    request.status.value,
                    json.dumps(request.to_dict(), sort_keys=True),
                    utc_now(),
                    request.request_id,
                ),
            )
            self._write_audit_event(
                connection,
                "access-request.denied",
                profile_id=profile_id,
                site_id=request.site_id,
                request_id=request.request_id,
                payload={"reason": reason, "actor": actor},
            )
        return request

    def begin_grant_exchange(self, request_id: str) -> tuple[SiteAccessRequest, AccessGrant, dict[str, str]]:
        profile_id, request = self.get_access_request(request_id)
        if request.status != AccessRequestStatus.APPROVED:
            raise ValueError("Request is not approved.")
        grant = self.get_access_grant(request_id)
        challenge = self.create_challenge(
            profile_id,
            request_id=request.request_id,
            site_id=request.site_id,
            grant_id=grant.grant_id,
            challenge_type="grant-exchange",
        )
        return request, grant, challenge

    def exchange_grant_session(
        self,
        request_id: str,
        *,
        challenge_id: str,
        signature: str,
        session_expires_at: str | None = None,
    ) -> tuple[AccessGrant, GrantSession]:
        profile_id, request = self.get_access_request(request_id)
        if request.status != AccessRequestStatus.APPROVED:
            raise ValueError("Request is not approved.")
        grant = self.get_access_grant(request_id)
        self.verify_challenge_response(
            profile_id,
            challenge_id,
            signature,
            request_id=request.request_id,
            site_id=request.site_id,
            grant_id=grant.grant_id,
            challenge_type="grant-exchange",
        )

        session = GrantSession.create(
            grant_id=grant.grant_id,
            profile_id=grant.profile_id,
            site_id=grant.site_id,
            approved_scopes=grant.approved_scopes,
            expires_at=session_expires_at or self._future_timestamp(DEFAULT_GRANT_SESSION_TTL_SECONDS),
        )
        session.extra_fields["request_id"] = request.request_id
        session.extra_fields["exchange_method"] = "challenge"

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO grant_sessions (
                    session_id, grant_id, request_id, profile_id, site_id, session_json, issued_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.grant_id,
                    request.request_id,
                    session.profile_id,
                    session.site_id,
                    json.dumps(session.to_dict(), sort_keys=True),
                    session.issued_at,
                    session.expires_at,
                ),
            )
            self._write_audit_event(
                connection,
                "grant-session.created",
                profile_id=profile_id,
                site_id=grant.site_id,
                request_id=request.request_id,
                grant_id=grant.grant_id,
                session_id=session.session_id,
                challenge_id=challenge_id,
                payload={"approved_scopes": list(session.approved_scopes)},
            )
        return grant, session

    def get_grant_session(self, session_id: str) -> GrantSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_json FROM grant_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Unknown grant session.")
        session = GrantSession.from_dict(json.loads(row["session_json"]))
        if self._is_expired(session.expires_at):
            raise ValueError("Grant session has expired.")
        return session

    def get_consented_projection(self, session_id: str) -> dict[str, Any]:
        session = self.get_grant_session(session_id)
        profile = self.get_profile(session.profile_id)
        if profile is None:
            raise KeyError("Profile not found.")

        projection = profile.consented_projection(
            session.approved_scopes,
            site_id=session.site_id,
            grant_id=session.grant_id,
            schema_version=session.schema_version,
        )
        with self._connect() as connection:
            self._write_audit_event(
                connection,
                "projection.read",
                profile_id=session.profile_id,
                site_id=session.site_id,
                grant_id=session.grant_id,
                session_id=session.session_id,
                payload={"approved_scopes": list(session.approved_scopes)},
            )
        return projection

    def _verified_profile(self, profile: ORFProfile) -> ORFProfile:
        if not profile.event_log:
            return profile

        rebuilt = ORFProfile(
            schema_version=profile.schema_version,
            profile_id=profile.profile_id,
            display_name=profile.display_name,
            public_key=profile.public_key,
            created_at=profile.created_at,
            updated_at=profile.created_at,
            consent=ConsentSettings(),
            sync=SyncState(device_id=profile.sync.device_id),
            event_log=[],
        )
        for event in sorted(
            profile.event_log,
            key=lambda item: (item.clock, item.timestamp, item.event_id),
        ):
            verify_signature(event.unsigned_payload(), event.signature, profile.public_key)
            rebuilt.apply_event(event)
        return rebuilt

    def get_profile(self, profile_id: str) -> ORFProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_json FROM profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        return ORFProfile.from_document(json.loads(row["document_json"]))

    def list_profiles(self) -> list[ORFProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM profiles ORDER BY updated_at DESC, profile_id ASC"
            ).fetchall()
        return [ORFProfile.from_document(json.loads(row["document_json"])) for row in rows]

    def save_profile(self, profile: ORFProfile) -> ORFProfile:
        profile = self._verified_profile(profile)
        document = json.dumps(profile.to_document(), sort_keys=True)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT public_key FROM profiles WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()
            if existing is not None and existing["public_key"] != profile.public_key:
                raise ValueError("Profile ID is already registered with a different public key.")

            connection.execute(
                """
                INSERT INTO profiles (profile_id, public_key, display_name, document_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    document_json = excluded.document_json,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.profile_id,
                    profile.public_key,
                    profile.display_name,
                    document,
                    profile.created_at,
                    profile.updated_at,
                ),
            )
            for event in profile.event_log:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO events (event_id, profile_id, clock, timestamp, event_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        profile.profile_id,
                        event.clock,
                        event.timestamp,
                        json.dumps(event.to_dict(), sort_keys=True),
                    ),
                )
        return profile

    def append_events(self, profile_id: str, events: list[SignedEvent]) -> ORFProfile:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown profile: {profile_id}")

        with self._connect() as connection:
            for event in events:
                verify_signature(event.unsigned_payload(), event.signature, profile.public_key)
                profile.apply_event(event)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO events (event_id, profile_id, clock, timestamp, event_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        profile.profile_id,
                        event.clock,
                        event.timestamp,
                        json.dumps(event.to_dict(), sort_keys=True),
                    ),
                )
            connection.execute(
                """
                UPDATE profiles
                SET display_name = ?, document_json = ?, updated_at = ?
                WHERE profile_id = ?
                """,
                (
                    profile.display_name,
                    json.dumps(profile.to_document(), sort_keys=True),
                    profile.updated_at,
                    profile.profile_id,
                ),
            )
        return profile

    def list_events(self, profile_id: str, after_clock: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json
                FROM events
                WHERE profile_id = ? AND clock > ?
                ORDER BY clock ASC, timestamp ASC, event_id ASC
                """,
                (profile_id, after_clock),
            ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def create_challenge(
        self,
        profile_id: str,
        *,
        request_id: str | None = None,
        site_id: str | None = None,
        grant_id: str | None = None,
        challenge_type: str = "profile",
    ) -> dict[str, str]:
        if self.get_profile(profile_id) is None:
            raise KeyError(f"Unknown profile: {profile_id}")

        challenge = {
            "challenge_id": secrets.token_urlsafe(12),
            "profile_id": profile_id,
            "nonce": secrets.token_urlsafe(24),
            "created_at": utc_now(),
        }
        if request_id is not None:
            challenge["request_id"] = request_id
        if site_id is not None:
            challenge["site_id"] = site_id
        if grant_id is not None:
            challenge["grant_id"] = grant_id
        if challenge_type != "profile":
            challenge["challenge_type"] = challenge_type
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO challenges (
                    challenge_id, profile_id, nonce, created_at, used, request_id, site_id, grant_id,
                    challenge_type
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    challenge["challenge_id"],
                    profile_id,
                    challenge["nonce"],
                    challenge["created_at"],
                    request_id,
                    site_id,
                    grant_id,
                    challenge_type,
                ),
            )
            self._write_audit_event(
                connection,
                "challenge.created",
                profile_id=profile_id,
                site_id=site_id,
                request_id=request_id,
                grant_id=grant_id,
                challenge_id=challenge["challenge_id"],
                payload={"challenge_type": challenge_type},
            )
        return challenge

    def verify_challenge_response(
        self,
        profile_id: str,
        challenge_id: str,
        signature: str,
        *,
        request_id: str | None = None,
        site_id: str | None = None,
        grant_id: str | None = None,
        challenge_type: str | None = None,
    ) -> bool:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise KeyError(f"Unknown profile: {profile_id}")

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT challenge_id, profile_id, nonce, created_at, used, request_id, site_id, grant_id, challenge_type
                FROM challenges
                WHERE challenge_id = ? AND profile_id = ?
                """,
                (challenge_id, profile_id),
            ).fetchone()
            if row is None:
                raise KeyError("Unknown challenge.")
            if row["used"]:
                raise ValueError("Challenge has already been used.")
            if self._challenge_expired(str(row["created_at"])):
                raise ValueError("Challenge has expired.")
            if request_id is not None and row["request_id"] != request_id:
                raise ValueError("Challenge is not bound to this access request.")
            if site_id is not None and row["site_id"] != site_id:
                raise ValueError("Challenge is not bound to this site.")
            if grant_id is not None and row["grant_id"] != grant_id:
                raise ValueError("Challenge is not bound to this grant.")
            if challenge_type is not None and row["challenge_type"] != challenge_type:
                raise ValueError("Challenge type does not match.")

            payload = {
                "challenge_id": row["challenge_id"],
                "profile_id": row["profile_id"],
                "nonce": row["nonce"],
                "created_at": row["created_at"],
            }
            if row["request_id"] is not None:
                payload["request_id"] = row["request_id"]
            if row["site_id"] is not None:
                payload["site_id"] = row["site_id"]
            if row["grant_id"] is not None:
                payload["grant_id"] = row["grant_id"]
            if row["challenge_type"] != "profile":
                payload["challenge_type"] = row["challenge_type"]
            verify_signature(payload, signature, profile.public_key)
            connection.execute(
                "UPDATE challenges SET used = 1 WHERE challenge_id = ?",
                (challenge_id,),
            )
            self._write_audit_event(
                connection,
                "challenge.verified",
                profile_id=profile_id,
                site_id=row["site_id"],
                request_id=row["request_id"],
                grant_id=row["grant_id"],
                challenge_id=challenge_id,
                payload={"challenge_type": row["challenge_type"]},
            )
        return True
