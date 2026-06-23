from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .crypto import fingerprint_public_key
from .recommender import AggregatedFeed, AggregatedRecommendation, RecommendationItem


CURRENT_PROFILE_SCHEMA_VERSION = "0.1.0"
CURRENT_CONTRACT_SCHEMA_VERSION = "0.3.0"
SELECTIVE_TOPIC_SCOPE_PREFIX = "topics.selective:"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_topic_name(topic: str) -> str:
    namespace, separator, path = topic.partition(":")
    if not separator or not namespace or not path:
        raise ValueError("Topic names must use namespaced format like 'orf:technology/python'.")
    for segment in [namespace, *path.split("/")]:
        if not segment or not all(char.islower() or char.isdigit() or char == "-" for char in segment):
            raise ValueError(
                "Topic segments may only contain lowercase letters, digits, and hyphens."
            )
    return topic


class Visibility(str, Enum):
    PUBLIC = "public"
    SELECTIVE = "selective"
    PRIVATE = "private"


class EventOp(str, Enum):
    SET_TOPIC = "set_topic"
    REMOVE_TOPIC = "remove_topic"
    SET_CONSENT = "set_consent"
    SET_OPT_OUT = "set_opt_out"
    SET_PROFILE = "set_profile"
    RECOMMEND = "recommend"


class AccessScope(str, Enum):
    PROFILE_READ = "profile.read"
    TOPICS_PUBLIC = "topics.public"
    CONSENT_SUMMARY = "consent.summary"


class AccessRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class SchemaCompatibility:
    requested_version: str
    current_version: str
    supported: bool
    allow_unknown_fields: bool
    ignore_unknown_scopes: bool


@dataclass(frozen=True, slots=True)
class ScopeDescriptor:
    scope: str
    topic: str | None = None


def parse_schema_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Schema versions must use semantic version format like '0.2.0'.")
    return tuple(int(part) for part in parts)


def schema_compatibility(
    version: str, *, current_version: str = CURRENT_CONTRACT_SCHEMA_VERSION
) -> SchemaCompatibility:
    try:
        requested_major, _, _ = parse_schema_version(version)
        current_major, _, _ = parse_schema_version(current_version)
    except ValueError:
        return SchemaCompatibility(
            requested_version=version,
            current_version=current_version,
            supported=False,
            allow_unknown_fields=False,
            ignore_unknown_scopes=False,
        )

    return SchemaCompatibility(
        requested_version=version,
        current_version=current_version,
        supported=requested_major == current_major,
        allow_unknown_fields=requested_major == current_major,
        ignore_unknown_scopes=requested_major == current_major,
    )


def ensure_supported_schema_version(
    version: str, *, current_version: str = CURRENT_CONTRACT_SCHEMA_VERSION
) -> str:
    compatibility = schema_compatibility(version, current_version=current_version)
    if not compatibility.supported:
        raise ValueError(
            f"Unsupported schema version '{version}'. Expected major version "
            f"{parse_schema_version(current_version)[0]}."
        )
    return version


def split_known_fields(
    data: Mapping[str, Any], *, known_fields: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    known: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in data.items():
        if key in known_fields:
            known[key] = value
        else:
            unknown[key] = value
    return known, unknown


def selective_topic_scope(topic: str) -> str:
    return f"{SELECTIVE_TOPIC_SCOPE_PREFIX}{validate_topic_name(topic)}"


def parse_scope(scope: str) -> ScopeDescriptor | None:
    if scope in {item.value for item in AccessScope}:
        return ScopeDescriptor(scope=scope)
    if scope.startswith(SELECTIVE_TOPIC_SCOPE_PREFIX):
        topic = scope.removeprefix(SELECTIVE_TOPIC_SCOPE_PREFIX)
        return ScopeDescriptor(scope=selective_topic_scope(topic), topic=validate_topic_name(topic))
    return None


def normalize_scope_set(
    scopes: Iterable[str], *, ignore_unknown: bool = True
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized: set[str] = set()
    unknown: set[str] = set()
    for raw_scope in scopes:
        scope = str(raw_scope)
        try:
            descriptor = parse_scope(scope)
        except ValueError:
            descriptor = None
        if descriptor is None:
            if not ignore_unknown:
                raise ValueError(f"Unknown scope: {scope}")
            unknown.add(scope)
            continue
        normalized.add(descriptor.scope)
    return tuple(sorted(normalized)), tuple(sorted(unknown))


def selective_topics_from_scopes(scopes: Iterable[str]) -> set[str]:
    normalized, _ = normalize_scope_set(scopes)
    topics = {
        descriptor.topic
        for scope in normalized
        if (descriptor := parse_scope(scope)) is not None and descriptor.topic is not None
    }
    return {topic for topic in topics if topic is not None}


def normalize_request_scope_sets(
    *,
    requested_scopes: Iterable[str] | None = None,
    required_scopes: Iterable[str] | None = None,
    optional_scopes: Iterable[str] | None = None,
    ignore_unknown_required: bool = False,
    ignore_unknown_optional: bool = True,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    normalized_required, unknown_required = normalize_scope_set(
        required_scopes or [],
        ignore_unknown=ignore_unknown_required,
    )
    if optional_scopes is None:
        normalized_optional, unknown_optional = normalize_scope_set(
            requested_scopes or [],
            ignore_unknown=ignore_unknown_optional,
        )
    else:
        normalized_optional, unknown_optional = normalize_scope_set(
            optional_scopes,
            ignore_unknown=ignore_unknown_optional,
        )
    optional_without_required = tuple(
        scope for scope in normalized_optional if scope not in set(normalized_required)
    )
    requested = tuple(sorted({*normalized_required, *optional_without_required}))
    return (
        normalized_required,
        optional_without_required,
        requested,
        unknown_required,
        unknown_optional,
    )


@dataclass(slots=True)
class SiteAccessRequest:
    schema_version: str
    request_id: str
    site_id: str
    site_name: str
    purpose: str
    requested_scopes: tuple[str, ...]
    required_scopes: tuple[str, ...]
    optional_scopes: tuple[str, ...]
    created_at: str
    expires_at: str | None = None
    status: AccessRequestStatus = AccessRequestStatus.PENDING
    extra_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        site_id: str,
        site_name: str,
        purpose: str,
        requested_scopes: Iterable[str] | None = None,
        required_scopes: Iterable[str] | None = None,
        optional_scopes: Iterable[str] | None = None,
        expires_at: str | None = None,
    ) -> "SiteAccessRequest":
        normalized_required, normalized_optional, normalized_scopes, _, _ = normalize_request_scope_sets(
            requested_scopes=requested_scopes,
            required_scopes=required_scopes,
            optional_scopes=optional_scopes,
        )
        return cls(
            schema_version=CURRENT_CONTRACT_SCHEMA_VERSION,
            request_id=str(uuid4()),
            site_id=site_id,
            site_name=site_name,
            purpose=purpose,
            requested_scopes=normalized_scopes,
            required_scopes=normalized_required,
            optional_scopes=normalized_optional,
            created_at=utc_now(),
            expires_at=expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "site_id": self.site_id,
            "site_name": self.site_name,
            "purpose": self.purpose,
            "requested_scopes": list(self.requested_scopes),
            "required_scopes": list(self.required_scopes),
            "optional_scopes": list(self.optional_scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            **self.extra_fields,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SiteAccessRequest":
        known, extra_fields = split_known_fields(
            data,
            known_fields={
                "schema_version",
                "request_id",
                "site_id",
                "site_name",
                "purpose",
                "requested_scopes",
                "required_scopes",
                "optional_scopes",
                "created_at",
                "expires_at",
                "status",
            },
        )
        ensure_supported_schema_version(str(known["schema_version"]))
        normalized_required, normalized_optional, normalized_scopes, _, _ = normalize_request_scope_sets(
            requested_scopes=known.get("requested_scopes", []),
            required_scopes=known.get("required_scopes", []),
            optional_scopes=known.get("optional_scopes"),
        )
        return cls(
            schema_version=str(known["schema_version"]),
            request_id=str(known["request_id"]),
            site_id=str(known["site_id"]),
            site_name=str(known["site_name"]),
            purpose=str(known["purpose"]),
            requested_scopes=normalized_scopes,
            required_scopes=normalized_required,
            optional_scopes=normalized_optional,
            created_at=str(known["created_at"]),
            expires_at=str(known["expires_at"]) if known.get("expires_at") is not None else None,
            status=AccessRequestStatus(str(known.get("status", AccessRequestStatus.PENDING.value))),
            extra_fields=dict(extra_fields),
        )


@dataclass(slots=True)
class AccessGrant:
    schema_version: str
    grant_id: str
    request_id: str
    profile_id: str
    site_id: str
    approved_scopes: tuple[str, ...]
    issued_at: str
    expires_at: str
    extra_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        profile_id: str,
        site_id: str,
        approved_scopes: Iterable[str],
        expires_at: str,
    ) -> "AccessGrant":
        normalized_scopes, _ = normalize_scope_set(approved_scopes)
        return cls(
            schema_version=CURRENT_CONTRACT_SCHEMA_VERSION,
            grant_id=str(uuid4()),
            request_id=request_id,
            profile_id=profile_id,
            site_id=site_id,
            approved_scopes=normalized_scopes,
            issued_at=utc_now(),
            expires_at=expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "grant_id": self.grant_id,
            "request_id": self.request_id,
            "profile_id": self.profile_id,
            "site_id": self.site_id,
            "approved_scopes": list(self.approved_scopes),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            **self.extra_fields,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccessGrant":
        known, extra_fields = split_known_fields(
            data,
            known_fields={
                "schema_version",
                "grant_id",
                "request_id",
                "profile_id",
                "site_id",
                "approved_scopes",
                "issued_at",
                "expires_at",
            },
        )
        ensure_supported_schema_version(str(known["schema_version"]))
        normalized_scopes, _ = normalize_scope_set(known.get("approved_scopes", []))
        return cls(
            schema_version=str(known["schema_version"]),
            grant_id=str(known["grant_id"]),
            request_id=str(known["request_id"]),
            profile_id=str(known["profile_id"]),
            site_id=str(known["site_id"]),
            approved_scopes=normalized_scopes,
            issued_at=str(known["issued_at"]),
            expires_at=str(known["expires_at"]),
            extra_fields=dict(extra_fields),
        )


@dataclass(slots=True)
class GrantSession:
    schema_version: str
    session_id: str
    grant_id: str
    profile_id: str
    site_id: str
    approved_scopes: tuple[str, ...]
    issued_at: str
    expires_at: str
    extra_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        grant_id: str,
        profile_id: str,
        site_id: str,
        approved_scopes: Iterable[str],
        expires_at: str,
    ) -> "GrantSession":
        normalized_scopes, _ = normalize_scope_set(approved_scopes)
        return cls(
            schema_version=CURRENT_CONTRACT_SCHEMA_VERSION,
            session_id=str(uuid4()),
            grant_id=grant_id,
            profile_id=profile_id,
            site_id=site_id,
            approved_scopes=normalized_scopes,
            issued_at=utc_now(),
            expires_at=expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "grant_id": self.grant_id,
            "profile_id": self.profile_id,
            "site_id": self.site_id,
            "approved_scopes": list(self.approved_scopes),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            **self.extra_fields,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GrantSession":
        known, extra_fields = split_known_fields(
            data,
            known_fields={
                "schema_version",
                "session_id",
                "grant_id",
                "profile_id",
                "site_id",
                "approved_scopes",
                "issued_at",
                "expires_at",
            },
        )
        ensure_supported_schema_version(str(known["schema_version"]))
        normalized_scopes, _ = normalize_scope_set(known.get("approved_scopes", []))
        return cls(
            schema_version=str(known["schema_version"]),
            session_id=str(known["session_id"]),
            grant_id=str(known["grant_id"]),
            profile_id=str(known["profile_id"]),
            site_id=str(known["site_id"]),
            approved_scopes=normalized_scopes,
            issued_at=str(known["issued_at"]),
            expires_at=str(known["expires_at"]),
            extra_fields=dict(extra_fields),
        )


@dataclass(slots=True)
class TopicPreference:
    topic: str
    weight: float
    visibility: Visibility
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "weight": self.weight,
            "visibility": self.visibility.value,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopicPreference":
        return cls(
            topic=validate_topic_name(str(data["topic"])),
            weight=float(data["weight"]),
            visibility=Visibility(data["visibility"]),
            updated_at=str(data["updated_at"]),
        )


@dataclass(slots=True)
class ConsentSettings:
    share_public_topics: bool = True
    ad_personalization: bool = True
    hosted_sync: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "share_public_topics": self.share_public_topics,
            "ad_personalization": self.ad_personalization,
            "hosted_sync": self.hosted_sync,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsentSettings":
        return cls(
            share_public_topics=bool(data.get("share_public_topics", True)),
            ad_personalization=bool(data.get("ad_personalization", True)),
            hosted_sync=bool(data.get("hosted_sync", True)),
        )


@dataclass(slots=True)
class SyncState:
    device_id: str
    last_clock: int = 0
    topic_clocks: dict[str, int] = field(default_factory=dict)
    consent_clocks: dict[str, int] = field(default_factory=dict)
    opt_out_clocks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "last_clock": self.last_clock,
            "topic_clocks": self.topic_clocks,
            "consent_clocks": self.consent_clocks,
            "opt_out_clocks": self.opt_out_clocks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncState":
        return cls(
            device_id=str(data["device_id"]),
            last_clock=int(data.get("last_clock", 0)),
            topic_clocks={str(key): int(value) for key, value in data.get("topic_clocks", {}).items()},
            consent_clocks={
                str(key): int(value) for key, value in data.get("consent_clocks", {}).items()
            },
            opt_out_clocks={
                str(key): int(value) for key, value in data.get("opt_out_clocks", {}).items()
            },
        )


@dataclass(slots=True)
class SignedEvent:
    event_id: str
    profile_id: str
    device_id: str
    clock: int
    timestamp: str
    op: EventOp
    payload: dict[str, Any]
    signature: str

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "profile_id": self.profile_id,
            "device_id": self.device_id,
            "clock": self.clock,
            "timestamp": self.timestamp,
            "op": self.op.value,
            "payload": self.payload,
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.unsigned_payload()
        data["signature"] = self.signature
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SignedEvent":
        return cls(
            event_id=str(data["event_id"]),
            profile_id=str(data["profile_id"]),
            device_id=str(data["device_id"]),
            clock=int(data["clock"]),
            timestamp=str(data["timestamp"]),
            op=EventOp(str(data["op"])),
            payload=dict(data["payload"]),
            signature=str(data["signature"]),
        )


@dataclass(slots=True)
class ORFProfile:
    schema_version: str
    profile_id: str
    display_name: str
    public_key: str
    created_at: str
    updated_at: str
    topics: dict[str, TopicPreference] = field(default_factory=dict)
    opt_out_topics: set[str] = field(default_factory=set)
    consent: ConsentSettings = field(default_factory=ConsentSettings)
    sync: SyncState = field(default_factory=lambda: SyncState(device_id="local"))
    event_log: list[SignedEvent] = field(default_factory=list)

    @classmethod
    def create(cls, display_name: str, public_key: str, device_id: str) -> "ORFProfile":
        now = utc_now()
        return cls(
            schema_version=CURRENT_PROFILE_SCHEMA_VERSION,
            profile_id=fingerprint_public_key(public_key),
            display_name=display_name,
            public_key=public_key,
            created_at=now,
            updated_at=now,
            sync=SyncState(device_id=device_id),
        )

    def next_clock(self) -> int:
        return self.sync.last_clock + 1

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "public_key": self.public_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "topics": [topic.to_dict() for topic in sorted(self.topics.values(), key=lambda item: item.topic)],
            "opt_out_topics": sorted(self.opt_out_topics),
            "consent": self.consent.to_dict(),
            "sync": self.sync.to_dict(),
            "event_log": [event.to_dict() for event in self.event_log],
        }

    @classmethod
    def from_document(cls, data: dict[str, Any]) -> "ORFProfile":
        ensure_supported_schema_version(
            str(data["schema_version"]), current_version=CURRENT_PROFILE_SCHEMA_VERSION
        )
        profile = cls(
            schema_version=str(data["schema_version"]),
            profile_id=str(data["profile_id"]),
            display_name=str(data["display_name"]),
            public_key=str(data["public_key"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            topics={
                item["topic"]: TopicPreference.from_dict(item) for item in data.get("topics", [])
            },
            opt_out_topics={validate_topic_name(topic) for topic in data.get("opt_out_topics", [])},
            consent=ConsentSettings.from_dict(data.get("consent", {})),
            sync=SyncState.from_dict(data.get("sync", {"device_id": "local"})),
            event_log=[SignedEvent.from_dict(item) for item in data.get("event_log", [])],
        )
        if profile.profile_id != fingerprint_public_key(profile.public_key):
            raise ValueError("Profile ID does not match embedded public key.")
        return profile

    def public_projection(self) -> dict[str, Any]:
        if not self.consent.share_public_topics:
            topics: list[dict[str, Any]] = []
        else:
            topics = [
                topic.to_dict()
                for topic in sorted(self.topics.values(), key=lambda item: item.topic)
                if topic.visibility == Visibility.PUBLIC and topic.topic not in self.opt_out_topics
            ]
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "topics": topics,
            "opt_out_topics": sorted(self.opt_out_topics),
            "consent": {
                "share_public_topics": self.consent.share_public_topics,
                "ad_personalization": self.consent.ad_personalization,
            },
            "updated_at": self.updated_at,
        }

    def consented_projection(
        self,
        approved_scopes: Iterable[str],
        *,
        site_id: str | None = None,
        grant_id: str | None = None,
        schema_version: str = CURRENT_CONTRACT_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        ensure_supported_schema_version(schema_version)
        normalized_scopes, unknown_scopes = normalize_scope_set(approved_scopes)
        scope_set = set(normalized_scopes)
        selective_topics = selective_topics_from_scopes(scope_set)

        topics: list[dict[str, Any]] = []
        for topic in sorted(self.topics.values(), key=lambda item: item.topic):
            if topic.topic in self.opt_out_topics:
                continue
            if topic.visibility == Visibility.PRIVATE:
                continue
            if topic.visibility == Visibility.PUBLIC:
                if (
                    self.consent.share_public_topics
                    and AccessScope.TOPICS_PUBLIC.value in scope_set
                ):
                    topics.append(topic.to_dict())
                continue
            if topic.visibility == Visibility.SELECTIVE and topic.topic in selective_topics:
                topics.append(topic.to_dict())

        projection: dict[str, Any] = {
            "schema_version": schema_version,
            "profile_id": self.profile_id,
            "granted_scopes": list(normalized_scopes),
            "topics": topics,
            "updated_at": self.updated_at,
        }
        if site_id is not None:
            projection["site_id"] = site_id
        if grant_id is not None:
            projection["grant_id"] = grant_id
        if AccessScope.PROFILE_READ.value in scope_set:
            projection["display_name"] = self.display_name
        if AccessScope.CONSENT_SUMMARY.value in scope_set:
            projection["consent"] = {
                "share_public_topics": self.consent.share_public_topics,
                "ad_personalization": self.consent.ad_personalization,
            }
        if unknown_scopes:
            projection["ignored_scopes"] = list(unknown_scopes)
        return projection

    def _append_event(self, event: SignedEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self.event_log):
            return
        self.event_log.append(event)

    def apply_event(self, event: SignedEvent) -> None:
        if event.profile_id != self.profile_id:
            raise ValueError("Event profile ID does not match profile.")

        self.sync.last_clock = max(self.sync.last_clock, event.clock)
        self.updated_at = max(self.updated_at, event.timestamp)

        if event.op == EventOp.SET_TOPIC:
            topic = validate_topic_name(str(event.payload["topic"]))
            current_clock = self.sync.topic_clocks.get(topic, -1)
            if event.clock >= current_clock:
                self.topics[topic] = TopicPreference(
                    topic=topic,
                    weight=float(event.payload["weight"]),
                    visibility=Visibility(str(event.payload["visibility"])),
                    updated_at=event.timestamp,
                )
            self.sync.topic_clocks[topic] = max(current_clock, event.clock)

        elif event.op == EventOp.REMOVE_TOPIC:
            topic = validate_topic_name(str(event.payload["topic"]))
            current_clock = self.sync.topic_clocks.get(topic, -1)
            if event.clock > current_clock:
                self.topics.pop(topic, None)
                self.sync.topic_clocks[topic] = event.clock

        elif event.op == EventOp.SET_CONSENT:
            field_name = str(event.payload["field"])
            value = bool(event.payload["value"])
            if not hasattr(self.consent, field_name):
                raise ValueError(f"Unknown consent field: {field_name}")
            current_clock = self.sync.consent_clocks.get(field_name, -1)
            current_value = bool(getattr(self.consent, field_name))
            should_apply = event.clock > current_clock or (
                event.clock == current_clock and current_value and not value
            )
            if should_apply:
                setattr(self.consent, field_name, value)
            self.sync.consent_clocks[field_name] = max(current_clock, event.clock)

        elif event.op == EventOp.SET_OPT_OUT:
            topic = validate_topic_name(str(event.payload["topic"]))
            value = bool(event.payload["value"])
            current_clock = self.sync.opt_out_clocks.get(topic, -1)
            currently_opted_out = topic in self.opt_out_topics
            should_apply = event.clock > current_clock or (
                event.clock == current_clock and not currently_opted_out and value
            )
            if should_apply:
                if value:
                    self.opt_out_topics.add(topic)
                else:
                    self.opt_out_topics.discard(topic)
            self.sync.opt_out_clocks[topic] = max(current_clock, event.clock)

        elif event.op == EventOp.SET_PROFILE:
            current_clock = self.sync.consent_clocks.get("_profile", -1)
            if event.clock >= current_clock and "display_name" in event.payload:
                self.display_name = str(event.payload["display_name"])
                self.sync.consent_clocks["_profile"] = event.clock

        self._append_event(event)


def build_signed_event(
    profile: ORFProfile,
    op: EventOp,
    payload: dict[str, Any],
    signature: str,
    *,
    clock: int | None = None,
    timestamp: str | None = None,
) -> SignedEvent:
    return SignedEvent(
        event_id=str(uuid4()),
        profile_id=profile.profile_id,
        device_id=profile.sync.device_id,
        clock=clock if clock is not None else profile.next_clock(),
        timestamp=timestamp or utc_now(),
        op=op,
        payload=payload,
        signature=signature,
    )

