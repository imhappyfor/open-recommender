from __future__ import annotations

import unittest

from open_recommender.crypto import generate_key_pair, sign_payload, verify_signature
from open_recommender.models import (
    AccessGrant,
    AccessRequestStatus,
    AccessScope,
    EventOp,
    GrantSession,
    ORFProfile,
    SignedEvent,
    SiteAccessRequest,
    build_signed_event,
    normalize_scope_set,
    schema_compatibility,
    selective_topic_scope,
)


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")

    def signed_event(self, op: EventOp, payload: dict, *, clock: int | None = None) -> SignedEvent:
        event = build_signed_event(self.profile, op, payload, signature="", clock=clock)
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        return event

    def test_public_projection_filters_private_topics(self) -> None:
        public_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        private_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.8, "visibility": "private"},
        )
        opt_out_event = self.signed_event(
            EventOp.SET_OPT_OUT,
            {"topic": "orf:politics/news", "value": True},
        )
        hidden_public_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:politics/news", "weight": 0.7, "visibility": "public"},
        )

        for event in [public_event, private_event, opt_out_event, hidden_public_event]:
            self.profile.apply_event(event)

        public_topics = {item["topic"] for item in self.profile.public_projection()["topics"]}
        self.assertEqual(public_topics, {"orf:technology/python"})

    def test_consent_revocation_wins_on_same_clock(self) -> None:
        grant = self.signed_event(
            EventOp.SET_CONSENT,
            {"field": "ad_personalization", "value": True},
            clock=3,
        )
        revoke = self.signed_event(
            EventOp.SET_CONSENT,
            {"field": "ad_personalization", "value": False},
            clock=3,
        )

        self.profile.apply_event(grant)
        self.profile.apply_event(revoke)

        self.assertFalse(self.profile.consent.ad_personalization)

    def test_topic_add_wins_on_same_clock(self) -> None:
        add = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/video", "weight": 0.5, "visibility": "public"},
            clock=5,
        )
        remove = self.signed_event(
            EventOp.REMOVE_TOPIC,
            {"topic": "orf:media/video"},
            clock=5,
        )

        self.profile.apply_event(add)
        self.profile.apply_event(remove)

        self.assertIn("orf:media/video", self.profile.topics)

    def test_signature_round_trip(self) -> None:
        event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:music/jazz", "weight": 0.6, "visibility": "public"},
        )
        self.assertTrue(verify_signature(event.unsigned_payload(), event.signature, self.profile.public_key))

    def test_consented_projection_respects_visibility_and_scopes(self) -> None:
        for event in [
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:health/sleep", "weight": 0.5, "visibility": "private"},
            ),
        ]:
            self.profile.apply_event(event)

        projection = self.profile.consented_projection(
            [
                AccessScope.PROFILE_READ.value,
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/podcasts"),
            ],
            site_id="pilot-site",
            grant_id="grant-123",
        )

        projected_topics = {item["topic"] for item in projection["topics"]}
        self.assertEqual(projected_topics, {"orf:technology/python", "orf:media/podcasts"})
        self.assertNotIn("orf:health/sleep", projected_topics)
        self.assertEqual(projection["display_name"], "Alice")
        self.assertEqual(projection["site_id"], "pilot-site")
        self.assertEqual(projection["grant_id"], "grant-123")

    def test_consented_projection_filters_unapproved_selective_topics_and_unknown_scopes(self) -> None:
        for event in [
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            ),
            self.signed_event(
                EventOp.SET_OPT_OUT,
                {"topic": "orf:technology/python", "value": True},
            ),
        ]:
            self.profile.apply_event(event)

        projection = self.profile.consented_projection(
            [
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/video"),
                "topics.experimental",
            ]
        )

        self.assertEqual(projection["topics"], [])
        self.assertEqual(projection["ignored_scopes"], ["topics.experimental"])

    def test_schema_compatibility_helpers_define_v0_behavior(self) -> None:
        compatible = schema_compatibility("0.2.5")
        incompatible = schema_compatibility("1.0.0")

        self.assertTrue(compatible.supported)
        self.assertTrue(compatible.allow_unknown_fields)
        self.assertTrue(compatible.ignore_unknown_scopes)
        self.assertFalse(incompatible.supported)

        normalized_scopes, unknown_scopes = normalize_scope_set(
            [AccessScope.TOPICS_PUBLIC.value, selective_topic_scope("orf:media/podcasts"), "unknown.scope"]
        )
        self.assertIn(AccessScope.TOPICS_PUBLIC.value, normalized_scopes)
        self.assertIn(selective_topic_scope("orf:media/podcasts"), normalized_scopes)
        self.assertEqual(unknown_scopes, ("unknown.scope",))

    def test_contract_shapes_preserve_unknown_fields(self) -> None:
        request = SiteAccessRequest.from_dict(
            {
                "schema_version": "0.2.0",
                "request_id": "request-1",
                "site_id": "site-1",
                "site_name": "Pilot Site",
                "purpose": "Personalize a home feed",
                "requested_scopes": [
                    AccessScope.PROFILE_READ.value,
                    selective_topic_scope("orf:media/podcasts"),
                    "site.beta",
                ],
                "created_at": "2025-01-01T00:00:00+00:00",
                "status": AccessRequestStatus.PENDING.value,
                "review_note": "manual pilot allowlist",
            }
        )
        grant = AccessGrant.from_dict(
            {
                "schema_version": "0.2.0",
                "grant_id": "grant-1",
                "request_id": request.request_id,
                "profile_id": self.profile.profile_id,
                "site_id": request.site_id,
                "approved_scopes": [AccessScope.PROFILE_READ.value],
                "issued_at": "2025-01-01T00:01:00+00:00",
                "expires_at": "2025-01-01T01:01:00+00:00",
                "exchange_method": "challenge",
            }
        )
        session = GrantSession.from_dict(
            {
                "schema_version": "0.2.0",
                "session_id": "session-1",
                "grant_id": grant.grant_id,
                "profile_id": self.profile.profile_id,
                "site_id": request.site_id,
                "approved_scopes": [AccessScope.PROFILE_READ.value],
                "issued_at": "2025-01-01T00:02:00+00:00",
                "expires_at": "2025-01-01T00:17:00+00:00",
                "transport": "bearer",
            }
        )

        self.assertEqual(
            request.requested_scopes,
            (AccessScope.PROFILE_READ.value, selective_topic_scope("orf:media/podcasts")),
        )
        self.assertEqual(request.extra_fields["review_note"], "manual pilot allowlist")
        self.assertEqual(grant.extra_fields["exchange_method"], "challenge")
        self.assertEqual(session.extra_fields["transport"], "bearer")


if __name__ == "__main__":
    unittest.main()
