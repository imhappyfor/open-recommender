from __future__ import annotations

import argparse
import base64
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .crypto import (
    generate_key_pair,
    load_private_key,
    load_private_key_bytes,
    private_key_public_key_b64,
    save_private_key,
    sign_payload,
)
from .models import EventOp, ORFProfile, build_signed_event
from .seed_catalog import SEED_SITES, SEED_TOPICS


DEFAULT_SEED_TOPIC_COUNT = 24
DEFAULT_SEED_RECOMMENDATION_COUNT = 180
DEFAULT_SEED_ACTIVITY_DAYS = 30

if len(SEED_TOPICS) < DEFAULT_SEED_TOPIC_COUNT:
    raise ValueError(
        "Seed topic catalog must contain at least "
        f"{DEFAULT_SEED_TOPIC_COUNT} topics, found {len(SEED_TOPICS)}."
    )


def load_profile(path: str | Path) -> ORFProfile:
    return ORFProfile.from_document(json.loads(Path(path).read_text(encoding="utf-8")))


def save_profile(path: str | Path, profile: ORFProfile) -> None:
    Path(path).write_text(json.dumps(profile.to_document(), indent=2, sort_keys=True), encoding="utf-8")


def private_key_path_for_profile(profile_path: Path, provided_path: str | None) -> Path:
    if provided_path:
        return Path(provided_path)
    return profile_path.with_suffix(profile_path.suffix + ".key")


def send_json(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, method=method, headers=headers)
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _apply_signed_event(
    profile: ORFProfile,
    private_key: Ed25519PrivateKey,
    op: EventOp,
    payload: dict,
    *,
    timestamp: str | None = None,
) -> None:
    event = build_signed_event(profile, op, payload, signature="", timestamp=timestamp)
    signature = sign_payload(event.unsigned_payload(), private_key)
    event.signature = signature
    profile.apply_event(event)


def create_event(profile: ORFProfile, private_key_path: Path, op: EventOp, payload: dict) -> None:
    private_key = load_private_key(private_key_path)
    _apply_signed_event(profile, private_key, op, payload)


def _seed_slug(topic: str) -> str:
    return topic.replace(":", "-").replace("/", "-")


def _seed_title(topic: str) -> str:
    _, _, path = topic.partition(":")
    return path.replace("/", " / ").replace("-", " ").title()


def _format_seed_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _phase_timestamps(
    start_at: datetime,
    end_at: datetime,
    count: int,
    rng: random.Random,
    *,
    jitter_minutes: int,
) -> list[str]:
    if count <= 0:
        return []
    if count == 1:
        return [_format_seed_timestamp(start_at)]
    total_seconds = max((end_at - start_at).total_seconds(), 1.0)
    scheduled: list[datetime] = []
    for index in range(count):
        fraction = index / (count - 1)
        anchor = start_at + timedelta(seconds=total_seconds * fraction)
        jitter = timedelta(minutes=rng.randint(-jitter_minutes, jitter_minutes))
        candidate = min(max(anchor + jitter, start_at), end_at)
        scheduled.append(candidate.replace(microsecond=0))
    scheduled.sort()
    normalized: list[datetime] = []
    last: datetime | None = None
    for candidate in scheduled:
        if last is not None and candidate <= last:
            candidate = last + timedelta(seconds=1)
        normalized.append(candidate)
        last = candidate
    return [_format_seed_timestamp(value) for value in normalized]


def seed_profile(
    profile: ORFProfile,
    private_key: Ed25519PrivateKey,
    *,
    seed_value: int | None = None,
    topic_count: int = DEFAULT_SEED_TOPIC_COUNT,
    recommendation_count: int = DEFAULT_SEED_RECOMMENDATION_COUNT,
    days: int = DEFAULT_SEED_ACTIVITY_DAYS,
) -> dict[str, object]:
    if topic_count <= 0:
        raise ValueError("Topic count must be positive.")
    if recommendation_count < 0:
        raise ValueError("Recommendation count cannot be negative.")
    if days <= 0:
        raise ValueError("Days must be positive.")

    resolved_seed = (
        seed_value
        if seed_value is not None
        else random.SystemRandom().randrange(1, 2**31)
    )
    rng = random.Random(resolved_seed)
    initial_event_count = len(profile.event_log)
    end_at = datetime.now(timezone.utc).replace(microsecond=0)
    start_at = end_at - timedelta(days=days)
    onboarding_end = start_at + timedelta(days=max(2, min(days // 5, 6)))
    recent_start = start_at + timedelta(days=max(4, days // 3))
    topic_pool = list(SEED_TOPICS)
    rng.shuffle(topic_pool)
    selected_topics = topic_pool[: min(topic_count, len(topic_pool))]

    public_count = max(3, len(selected_topics) // 3)
    selective_count = max(3, len(selected_topics) // 3)
    private_count = max(0, len(selected_topics) - public_count - selective_count)
    visibility_order = (
        ["public"] * public_count
        + ["selective"] * selective_count
        + ["private"] * private_count
    )[: len(selected_topics)]
    rng.shuffle(visibility_order)

    seeded_topics: list[tuple[str, str]] = []
    public_topics: list[str] = []
    feed_topics: list[str] = []
    topic_state: dict[str, dict[str, object]] = {}
    initial_topic_timestamps = _phase_timestamps(
        start_at,
        onboarding_end,
        len(selected_topics),
        rng,
        jitter_minutes=180,
    )
    for topic, visibility, timestamp in zip(
        selected_topics,
        visibility_order,
        initial_topic_timestamps,
        strict=False,
    ):
        weight = round(rng.uniform(0.35, 0.98), 2)
        _apply_signed_event(
            profile,
            private_key,
            EventOp.SET_TOPIC,
            {"topic": topic, "weight": weight, "visibility": visibility},
            timestamp=timestamp,
        )
        seeded_topics.append((topic, visibility))
        topic_state[topic] = {"weight": weight, "visibility": visibility}
        if visibility == "public":
            public_topics.append(topic)
            feed_topics.append(topic)
        elif visibility == "selective":
            feed_topics.append(topic)

    opt_out_topics: list[str] = []
    if public_topics:
        opt_out_count = min(max(1, len(public_topics) // 4), len(public_topics))
        opt_out_topics = sorted(rng.sample(public_topics, opt_out_count))
        for topic, timestamp in zip(
            opt_out_topics,
            _phase_timestamps(start_at, recent_start, len(opt_out_topics), rng, jitter_minutes=240),
            strict=False,
        ):
            _apply_signed_event(
                profile,
                private_key,
                EventOp.SET_OPT_OUT,
                {"topic": topic, "value": True},
                timestamp=timestamp,
            )

    consent_values = {
        "share_public_topics": True,
        "ad_personalization": rng.choice([True, False]),
        "hosted_sync": rng.choice([True, False]),
    }
    consent_timestamps = _phase_timestamps(
        start_at,
        recent_start,
        len(consent_values),
        rng,
        jitter_minutes=300,
    )
    for (field_name, value), timestamp in zip(consent_values.items(), consent_timestamps, strict=False):
        _apply_signed_event(
            profile,
            private_key,
            EventOp.SET_CONSENT,
            {"field": field_name, "value": value},
            timestamp=timestamp,
        )

    topic_update_events_added = max(topic_count, days)
    topic_update_timestamps = _phase_timestamps(
        onboarding_end,
        end_at,
        topic_update_events_added,
        rng,
        jitter_minutes=360,
    )
    for timestamp in topic_update_timestamps:
        topic = rng.choice(selected_topics)
        state = topic_state[topic]
        updated_weight = round(
            min(0.99, max(0.2, float(state["weight"]) + rng.uniform(-0.18, 0.18))),
            2,
        )
        state["weight"] = updated_weight
        _apply_signed_event(
            profile,
            private_key,
            EventOp.SET_TOPIC,
            {
                "topic": topic,
                "weight": updated_weight,
                "visibility": str(state["visibility"]),
            },
            timestamp=timestamp,
        )

    recommendation_events_added = 0
    if recommendation_count > 0:
        if not feed_topics:
            feed_topics = selected_topics[:]
        rng.shuffle(feed_topics)
        recommendation_timestamps = _phase_timestamps(
            recent_start,
            end_at,
            recommendation_count,
            rng,
            jitter_minutes=420,
        )
        item_index = 1
        timestamp_index = 0
        while recommendation_events_added < recommendation_count and timestamp_index < len(
            recommendation_timestamps
        ):
            topic = feed_topics[(item_index - 1) % len(feed_topics)]
            site_count = min(
                recommendation_count - recommendation_events_added,
                rng.randint(1, min(3, len(SEED_SITES))),
            )
            chosen_sites = rng.sample(SEED_SITES, site_count)
            item_id = f"seed-{_seed_slug(topic)}-{item_index}"
            for site_id, site_name in chosen_sites:
                timestamp = recommendation_timestamps[timestamp_index]
                _apply_signed_event(
                    profile,
                    private_key,
                    EventOp.RECOMMEND,
                    {
                        "item_id": item_id,
                        "site_id": site_id,
                        "site_name": site_name,
                        "score": round(rng.uniform(0.55, 0.98), 2),
                        "reason": f"Seeded sample tied to {topic}.",
                        "metadata": {
                            "title": f"{_seed_title(topic)} Digest {item_index}",
                            "topic": topic,
                            "seeded": True,
                        },
                        "timestamp": timestamp,
                    },
                    timestamp=timestamp,
                )
                recommendation_events_added += 1
                timestamp_index += 1
                if recommendation_events_added >= recommendation_count:
                    break
            item_index += 1

    visibility_counts = {
        visibility: sum(1 for _, item_visibility in seeded_topics if item_visibility == visibility)
        for visibility in ("public", "selective", "private")
    }
    seeded_events = profile.event_log[initial_event_count:]
    total_events_added = len(seeded_events)
    first_event_at = seeded_events[0].timestamp if seeded_events else None
    last_event_at = seeded_events[-1].timestamp if seeded_events else None
    if first_event_at is not None and first_event_at < profile.created_at:
        profile.created_at = first_event_at
    return {
        "seed_value": resolved_seed,
        "days_simulated": days,
        "topics_added": len(seeded_topics),
        "topic_update_events_added": topic_update_events_added,
        "visibility_counts": visibility_counts,
        "opt_outs_added": len(opt_out_topics),
        "opt_out_topics": opt_out_topics,
        "recommendation_events_added": recommendation_events_added,
        "total_events_added": total_events_added,
        "first_event_at": first_event_at,
        "last_event_at": last_event_at,
        "consent": consent_values,
    }


def command_create(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    key_path = private_key_path_for_profile(profile_path, args.key_path)
    private_key, public_key = generate_key_pair()
    profile = ORFProfile.create(
        display_name=args.display_name,
        public_key=public_key,
        device_id=args.device_id,
    )
    seed_summary: dict[str, object] | None = None
    if args.seed:
        seed_summary = seed_profile(
            profile,
            private_key,
            seed_value=args.seed_value,
            topic_count=args.topic_count,
            recommendation_count=args.recommendation_count,
            days=args.days,
        )
    save_profile(profile_path, profile)
    save_private_key(key_path, private_key, passphrase=args.passphrase)
    payload = {
        "profile_path": str(profile_path),
        "key_path": str(key_path),
        "profile_id": profile.profile_id,
    }
    if seed_summary is not None:
        payload["seed"] = seed_summary
    print(json.dumps(payload, indent=2))
    return 0


def command_topic_set(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    create_event(
        profile,
        private_key_path_for_profile(profile_path, args.key_path),
        EventOp.SET_TOPIC,
        {"topic": args.topic, "weight": args.weight, "visibility": args.visibility},
    )
    save_profile(profile_path, profile)
    return 0


def command_topic_remove(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    create_event(
        profile,
        private_key_path_for_profile(profile_path, args.key_path),
        EventOp.REMOVE_TOPIC,
        {"topic": args.topic},
    )
    save_profile(profile_path, profile)
    return 0


def command_consent_set(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    create_event(
        profile,
        private_key_path_for_profile(profile_path, args.key_path),
        EventOp.SET_CONSENT,
        {"field": args.field, "value": args.value.lower() == "true"},
    )
    save_profile(profile_path, profile)
    return 0


def command_opt_out(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    create_event(
        profile,
        private_key_path_for_profile(profile_path, args.key_path),
        EventOp.SET_OPT_OUT,
        {"topic": args.topic, "value": args.value.lower() == "true"},
    )
    save_profile(profile_path, profile)
    return 0


def command_export_public(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile_path)
    print_json(profile.public_projection())
    return 0


def command_sync_push(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile_path)
    send_json("POST", f"{args.server.rstrip('/')}/profiles", {"profile": profile.to_document()})
    send_json(
        "POST",
        f"{args.server.rstrip('/')}/profiles/{profile.profile_id}/events",
        {"events": [event.to_dict() for event in profile.event_log]},
    )
    return 0


def command_sync_pull(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    after_clock = max(profile.sync.last_clock - 1, 0)
    payload = send_json(
        "GET",
        f"{args.server.rstrip('/')}/profiles/{profile.profile_id}/events?after_clock={after_clock}",
    )
    known_event_ids = {event.event_id for event in profile.event_log}
    for event_data in payload["events"]:
        if event_data["event_id"] in known_event_ids:
            continue
        profile.apply_event(build_event_from_remote(event_data))
    save_profile(profile_path, profile)
    return 0


def build_event_from_remote(event_data: dict) -> object:
    from .models import SignedEvent

    return SignedEvent.from_dict(event_data)


def command_site_access_request_get(args: argparse.Namespace) -> int:
    print_json(send_json("GET", f"{args.server.rstrip('/')}/site-access-requests/{args.request_id}"))
    return 0


def command_site_access_request_approve(args: argparse.Namespace) -> int:
    payload: dict[str, object] = {"actor": args.actor}
    if args.scope is not None:
        payload["approved_scopes"] = args.scope
    if args.grant_expires_at is not None:
        payload["grant_expires_at"] = args.grant_expires_at
    print_json(send_json("POST", f"{args.server.rstrip('/')}/site-access-requests/{args.request_id}/approve", payload))
    return 0


def command_site_access_request_deny(args: argparse.Namespace) -> int:
    payload: dict[str, object] = {"actor": args.actor}
    if args.reason is not None:
        payload["reason"] = args.reason
    print_json(send_json("POST", f"{args.server.rstrip('/')}/site-access-requests/{args.request_id}/deny", payload))
    return 0


def command_grant_session_projection(args: argparse.Namespace) -> int:
    print_json(send_json("GET", f"{args.server.rstrip('/')}/grant-sessions/{args.session_id}/projection"))
    return 0


def _default_backup_key_path(backup_path: Path) -> Path:
    return backup_path.with_suffix(backup_path.suffix + ".key")


def command_backup_create(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    key_path = private_key_path_for_profile(profile_path, args.key_path)
    private_key = load_private_key(key_path, passphrase=args.key_passphrase)
    profile_doc = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = ORFProfile.from_document(profile_doc)

    if private_key_public_key_b64(private_key) != profile.public_key:
        raise ValueError("The provided key does not match the profile public key.")
    if not args.backup_passphrase:
        raise ValueError("Backup passphrase is required so the bundled key is encrypted at rest.")

    encrypted_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(args.backup_passphrase.encode("utf-8")),
    )
    backup_doc = {
        "backup_schema": "orf-backup.v1",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "profile": profile_doc,
        "private_key": {
            "encoding": "pem-pkcs8",
            "encrypted": True,
            "pem_b64": base64.b64encode(encrypted_key_bytes).decode("ascii"),
        },
    }
    backup_path = Path(args.backup_path)
    backup_path.write_text(json.dumps(backup_doc, indent=2, sort_keys=True), encoding="utf-8")
    print_json(
        {
            "backup_path": str(backup_path),
            "profile_id": profile.profile_id,
            "key_encrypted": True,
        }
    )
    return 0


def command_backup_restore(args: argparse.Namespace) -> int:
    backup_path = Path(args.backup_path)
    backup_doc = json.loads(backup_path.read_text(encoding="utf-8"))
    if backup_doc.get("backup_schema") != "orf-backup.v1":
        raise ValueError("Unsupported backup schema.")

    profile_doc = backup_doc.get("profile")
    private_key_payload = backup_doc.get("private_key")
    if not isinstance(profile_doc, dict) or not isinstance(private_key_payload, dict):
        raise ValueError("Backup must include 'profile' and 'private_key' objects.")
    if private_key_payload.get("encoding") != "pem-pkcs8":
        raise ValueError("Unsupported private key encoding in backup.")
    if not bool(private_key_payload.get("encrypted", False)):
        raise ValueError("Unencrypted backup keys are not supported.")
    if not args.backup_passphrase:
        raise ValueError("Backup passphrase is required to decrypt this backup key.")

    profile = ORFProfile.from_document(profile_doc)
    key_bytes = base64.b64decode(str(private_key_payload.get("pem_b64", "")).encode("ascii"))
    private_key = load_private_key_bytes(key_bytes, passphrase=args.backup_passphrase)
    if private_key_public_key_b64(private_key) != profile.public_key:
        raise ValueError("Backup key does not match the profile in this backup.")

    profile_path = Path(args.profile_path)
    key_path = Path(args.key_path) if args.key_path else _default_backup_key_path(profile_path)
    for path in (profile_path, key_path):
        if path.exists() and not args.overwrite:
            raise ValueError(f"Refusing to overwrite existing file: {path}")

    profile_path.write_text(json.dumps(profile_doc, indent=2, sort_keys=True), encoding="utf-8")
    key_path.write_bytes(key_bytes)
    print_json(
        {
            "profile_path": str(profile_path),
            "key_path": str(key_path),
            "profile_id": profile.profile_id,
            "restored_from": str(backup_path),
        }
    )
    return 0


def command_feed_show(args: argparse.Namespace) -> int:
    """Display aggregated cross-site recommendation feed."""
    from .recommender import AggregatedFeed

    profile_path = Path(args.profile_path)
    profile = load_profile(profile_path)
    feed = AggregatedFeed(profile)
    top_n = args.top_n if args.top_n else 20

    recs = feed.top_n(top_n)

    output = {
        "profile_id": profile.profile_id,
        "feed_size": len(recs),
        "top_n": top_n,
        "recommendations": [r.to_dict() for r in recs],
    }
    print_json(output)
    return 0


def command_seed(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile_path)
    key_path = private_key_path_for_profile(profile_path, args.key_path)
    profile = load_profile(profile_path)
    private_key = load_private_key(
        key_path,
        passphrase=args.key_passphrase,
    )
    if private_key_public_key_b64(private_key) != profile.public_key:
        raise ValueError("The provided key does not match the profile public key.")
    summary = seed_profile(
        profile,
        private_key,
        seed_value=args.seed_value,
        topic_count=args.topic_count,
        recommendation_count=args.recommendation_count,
        days=args.days,
    )
    save_profile(profile_path, profile)
    print_json(
        {
            "profile_path": str(profile_path),
            "profile_id": profile.profile_id,
            "seed": summary,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-recommender")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("profile_path")
    create_parser.add_argument("--display-name", required=True)
    create_parser.add_argument("--device-id", default="local-device")
    create_parser.add_argument("--key-path")
    create_parser.add_argument("--passphrase")
    create_parser.add_argument(
        "--seed",
        action="store_true",
        help="Populate the new profile with randomized sample topics, consent, and recommendation events.",
    )
    create_parser.add_argument(
        "--seed-value",
        type=int,
        help="Optional integer seed for reproducible sample data.",
    )
    create_parser.add_argument(
        "--topic-count",
        type=int,
        default=DEFAULT_SEED_TOPIC_COUNT,
        help=f"How many sample topics to add when seeding (default: {DEFAULT_SEED_TOPIC_COUNT}).",
    )
    create_parser.add_argument(
        "--recommendation-count",
        type=int,
        default=DEFAULT_SEED_RECOMMENDATION_COUNT,
        help=(
            "How many seeded recommendation events to add when seeding "
            f"(default: {DEFAULT_SEED_RECOMMENDATION_COUNT})."
        ),
    )
    create_parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_SEED_ACTIVITY_DAYS,
        help=f"How many days of activity to simulate when seeding (default: {DEFAULT_SEED_ACTIVITY_DAYS}).",
    )
    create_parser.set_defaults(func=command_create)

    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("profile_path")
    seed_parser.add_argument("--key-path")
    seed_parser.add_argument("--key-passphrase")
    seed_parser.add_argument(
        "--seed-value",
        type=int,
        help="Optional integer seed for reproducible sample data.",
    )
    seed_parser.add_argument(
        "--topic-count",
        type=int,
        default=DEFAULT_SEED_TOPIC_COUNT,
        help=f"How many sample topics to add (default: {DEFAULT_SEED_TOPIC_COUNT}).",
    )
    seed_parser.add_argument(
        "--recommendation-count",
        type=int,
        default=DEFAULT_SEED_RECOMMENDATION_COUNT,
        help=(
            "How many seeded recommendation events to add "
            f"(default: {DEFAULT_SEED_RECOMMENDATION_COUNT})."
        ),
    )
    seed_parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_SEED_ACTIVITY_DAYS,
        help=f"How many days of activity to simulate (default: {DEFAULT_SEED_ACTIVITY_DAYS}).",
    )
    seed_parser.set_defaults(func=command_seed)

    topic_parser = subparsers.add_parser("topic-set")
    topic_parser.add_argument("profile_path")
    topic_parser.add_argument("topic")
    topic_parser.add_argument("weight", type=float)
    topic_parser.add_argument("--visibility", choices=["public", "selective", "private"], default="public")
    topic_parser.add_argument("--key-path")
    topic_parser.set_defaults(func=command_topic_set)

    remove_parser = subparsers.add_parser("topic-remove")
    remove_parser.add_argument("profile_path")
    remove_parser.add_argument("topic")
    remove_parser.add_argument("--key-path")
    remove_parser.set_defaults(func=command_topic_remove)

    consent_parser = subparsers.add_parser("consent-set")
    consent_parser.add_argument("profile_path")
    consent_parser.add_argument("field", choices=["share_public_topics", "ad_personalization", "hosted_sync"])
    consent_parser.add_argument("value", choices=["true", "false"])
    consent_parser.add_argument("--key-path")
    consent_parser.set_defaults(func=command_consent_set)

    opt_out_parser = subparsers.add_parser("opt-out-set")
    opt_out_parser.add_argument("profile_path")
    opt_out_parser.add_argument("topic")
    opt_out_parser.add_argument("value", choices=["true", "false"])
    opt_out_parser.add_argument("--key-path")
    opt_out_parser.set_defaults(func=command_opt_out)

    export_parser = subparsers.add_parser("export-public")
    export_parser.add_argument("profile_path")
    export_parser.set_defaults(func=command_export_public)

    push_parser = subparsers.add_parser("sync-push")
    push_parser.add_argument("profile_path")
    push_parser.add_argument("server")
    push_parser.set_defaults(func=command_sync_push)

    pull_parser = subparsers.add_parser("sync-pull")
    pull_parser.add_argument("profile_path")
    pull_parser.add_argument("server")
    pull_parser.set_defaults(func=command_sync_pull)

    site_request_get_parser = subparsers.add_parser("site-access-request-get")
    site_request_get_parser.add_argument("request_id")
    site_request_get_parser.add_argument("server")
    site_request_get_parser.set_defaults(func=command_site_access_request_get)

    site_request_approve_parser = subparsers.add_parser("site-access-request-approve")
    site_request_approve_parser.add_argument("request_id")
    site_request_approve_parser.add_argument("server")
    site_request_approve_parser.add_argument("--scope", action="append")
    site_request_approve_parser.add_argument("--grant-expires-at")
    site_request_approve_parser.add_argument("--actor", default="cli")
    site_request_approve_parser.set_defaults(func=command_site_access_request_approve)

    site_request_deny_parser = subparsers.add_parser("site-access-request-deny")
    site_request_deny_parser.add_argument("request_id")
    site_request_deny_parser.add_argument("server")
    site_request_deny_parser.add_argument("--reason")
    site_request_deny_parser.add_argument("--actor", default="cli")
    site_request_deny_parser.set_defaults(func=command_site_access_request_deny)

    grant_session_projection_parser = subparsers.add_parser("grant-session-projection")
    grant_session_projection_parser.add_argument("session_id")
    grant_session_projection_parser.add_argument("server")
    grant_session_projection_parser.set_defaults(func=command_grant_session_projection)

    backup_create_parser = subparsers.add_parser("backup-create")
    backup_create_parser.add_argument("profile_path")
    backup_create_parser.add_argument("backup_path")
    backup_create_parser.add_argument("--key-path")
    backup_create_parser.add_argument("--key-passphrase")
    backup_create_parser.add_argument("--backup-passphrase", required=True)
    backup_create_parser.set_defaults(func=command_backup_create)

    backup_restore_parser = subparsers.add_parser("backup-restore")
    backup_restore_parser.add_argument("backup_path")
    backup_restore_parser.add_argument("profile_path")
    backup_restore_parser.add_argument("--key-path")
    backup_restore_parser.add_argument("--backup-passphrase", required=True)
    backup_restore_parser.add_argument("--overwrite", action="store_true")
    backup_restore_parser.set_defaults(func=command_backup_restore)

    feed_parser = subparsers.add_parser("feed")
    feed_subparsers = feed_parser.add_subparsers(dest="feed_command", required=True)

    feed_show_parser = feed_subparsers.add_parser("show")
    feed_show_parser.add_argument("profile_path")
    feed_show_parser.add_argument("--top-n", type=int, default=20)
    feed_show_parser.set_defaults(func=command_feed_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        print(detail or str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
