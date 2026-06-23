from __future__ import annotations
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.service import create_app


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "service.db"
        self.app = create_app(self.db_path)
        self.client = TestClient(self.app)
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def signed_event(self, op: EventOp, payload: dict) -> dict:
        event = build_signed_event(self.profile, op, payload, signature="")
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        self.profile.apply_event(event)
        return event.to_dict()

    def update_challenge_created_at(self, challenge_id: str, created_at: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE challenges SET created_at = ? WHERE challenge_id = ?",
                (created_at, challenge_id),
            )

    def test_profile_registration_and_public_read(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.4, "visibility": "private"},
        )

        response = self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.assertEqual(response.status_code, 200)

        public_response = self.client.get(f"/profiles/{self.profile.profile_id}/public")
        self.assertEqual(public_response.status_code, 200)
        body = public_response.json()
        self.assertEqual([topic["topic"] for topic in body["topics"]], ["orf:technology/python"])
        health_response = self.client.get("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["status"], "ok")
        self.assertIn("service", health_response.json())

    def test_local_browser_cors_allows_preflight_and_post_from_react(self) -> None:
        origin = "http://localhost:5173"
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        preflight_response = self.client.options(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(preflight_response.status_code, 200)
        self.assertEqual(preflight_response.headers.get("access-control-allow-origin"), origin)

        post_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            headers={"Origin": origin},
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["profile.read"],
            },
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(post_response.headers.get("access-control-allow-origin"), origin)

    def test_events_and_challenge_flow(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        event = build_signed_event(
            self.profile,
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "public"},
            signature="",
        )
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)

        event_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/events",
            json={"events": [event.to_dict()]},
        )
        self.assertEqual(event_response.status_code, 200)

        pull_response = self.client.get(f"/profiles/{self.profile.profile_id}/events?after_clock=0")
        self.assertEqual(pull_response.status_code, 200)
        self.assertEqual(len(pull_response.json()["events"]), 1)

        challenge_response = self.client.post(f"/profiles/{self.profile.profile_id}/challenges")
        challenge = challenge_response.json()
        challenge_signature = sign_payload(
            {
                "challenge_id": challenge["challenge_id"],
                "profile_id": challenge["profile_id"],
                "nonce": challenge["nonce"],
                "created_at": challenge["created_at"],
            },
            self.private_key,
        )
        verify_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/challenge-response",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": challenge_signature,
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertTrue(verify_response.json()["verified"])

    def test_challenge_verify_returns_explicit_error_for_bad_signature(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        challenge_response = self.client.post(f"/profiles/{self.profile.profile_id}/challenges")
        challenge = challenge_response.json()

        wrong_private_key, _ = generate_key_pair()
        bad_signature = sign_payload(
            {
                "challenge_id": challenge["challenge_id"],
                "profile_id": challenge["profile_id"],
                "nonce": challenge["nonce"],
                "created_at": challenge["created_at"],
            },
            wrong_private_key,
        )

        verify_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/challenge-response",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": bad_signature,
            },
        )
        self.assertEqual(verify_response.status_code, 400)
        self.assertEqual(verify_response.json()["detail"], "Signature verification failed.")

    def test_demo_flow_personalizes_before_and_after_verification(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.5, "visibility": "private"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        demo_response = self.client.get(f"/demo/site/{self.profile.profile_id}")
        self.assertEqual(demo_response.status_code, 200)
        demo_body = demo_response.json()
        self.assertFalse(demo_body["demo"]["site_account_required"])
        self.assertFalse(demo_body["demo"]["verified"])
        self.assertEqual(
            demo_body["personalization"]["featured_topics"],
            ["orf:technology/python", "orf:media/podcasts"],
        )
        self.assertEqual(
            [item["item_id"] for item in demo_body["personalization"]["recommendations"]],
            ["demo-technology-python", "demo-media-podcasts"],
        )
        self.assertNotIn("orf:health/sleep", demo_body["personalization"]["featured_topics"])

        challenge_response = self.client.post(f"/demo/site/{self.profile.profile_id}/challenge")
        self.assertEqual(challenge_response.status_code, 200)
        challenge_body = challenge_response.json()
        signature = sign_payload(challenge_body["challenge_payload"], self.private_key)

        verify_response = self.client.post(
            f"/demo/site/{self.profile.profile_id}/verify",
            json={
                "challenge_id": challenge_body["challenge"]["challenge_id"],
                "signature": signature,
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        verified_body = verify_response.json()
        self.assertTrue(verified_body["demo"]["verified"])
        self.assertEqual(verified_body["demo"]["proof"], "challenge-signature")
        self.assertTrue(verified_body["session"]["portable_profile_session"])
        self.assertTrue(verified_body["session"]["can_save_state_without_account"])
        self.assertEqual(verified_body["personalization"]["mode"], "verified-profile")

    def test_site_access_request_grant_session_projection_flow(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.5, "visibility": "private"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": [
                    "profile.read",
                    "topics.public",
                    "topics.selective:orf:media/podcasts",
                    "topics.selective:orf:health/sleep",
                    "unknown.scope",
                ],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_body = request_response.json()["access_request"]
        request_id = request_body["request_id"]
        self.assertEqual(
            request_body["ignored_requested_scopes"],
            ["unknown.scope"],
        )

        approve_response = self.client.post(
            f"/site-access-requests/{request_id}/approve",
            json={
                "approved_scopes": [
                    "profile.read",
                    "topics.public",
                    "topics.selective:orf:media/podcasts",
                ]
            },
        )
        self.assertEqual(approve_response.status_code, 200)
        approve_body = approve_response.json()
        self.assertEqual(
            approve_body["grant"]["approved_scopes"],
            [
                "profile.read",
                "topics.public",
                "topics.selective:orf:media/podcasts",
            ],
        )

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()
        challenge_payload = exchange_body["challenge_payload"]
        challenge_signature = sign_payload(challenge_payload, self.private_key)

        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": challenge_signature,
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        verify_body = verify_response.json()
        self.assertTrue(verify_body["verified"])
        session_id = verify_body["session"]["session_id"]

        projection_response = self.client.get(f"/grant-sessions/{session_id}/projection")
        self.assertEqual(projection_response.status_code, 200)
        projection_body = projection_response.json()
        projection = projection_body["projection"]
        self.assertEqual(projection["site_id"], "open-news-demo")
        self.assertEqual(projection["grant_id"], verify_body["grant"]["grant_id"])
        self.assertEqual(projection["display_name"], "Alice")
        self.assertEqual(
            [topic["topic"] for topic in projection["topics"]],
            ["orf:media/podcasts", "orf:technology/python"],
        )
        self.assertNotIn("consent", projection)
        self.assertNotIn("orf:health/sleep", [topic["topic"] for topic in projection["topics"]])

        replay_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": challenge_signature,
            },
        )
        self.assertEqual(replay_response.status_code, 400)
        self.assertIn("already been used", replay_response.json()["detail"])

    def test_grant_session_rank_reranks_without_leaking_profile_state(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.5, "visibility": "private"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.selective:orf:media/podcasts"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = self.client.post(
            f"/site-access-requests/{request_id}/approve",
            json={"approved_scopes": ["topics.selective:orf:media/podcasts"]},
        )
        self.assertEqual(approve_response.status_code, 200)

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()

        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": sign_payload(
                    exchange_body["challenge_payload"],
                    self.private_key,
                ),
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        session_id = verify_response.json()["session"]["session_id"]

        rank_response = self.client.post(
            f"/grant-sessions/{session_id}/rank",
            json={
                "schema_version": "0.3.0",
                "top_n": 2,
                "include_debug": True,
                "candidates": [
                    {
                        "candidate_id": "podcast-feature",
                        "site_score": 0.7,
                        "candidate_topics": ["orf:media/podcasts"],
                        "metadata": {"slot": "hero"},
                    },
                    {
                        "candidate_id": "tech-feature",
                        "site_score": 0.8,
                        "candidate_topics": ["orf:technology/python"],
                        "metadata": {"slot": "secondary"},
                    },
                ],
            },
        )
        self.assertEqual(rank_response.status_code, 200)
        rank_body = rank_response.json()
        ranking = rank_body["ranking"]
        self.assertEqual(ranking["candidate_count"], 2)
        self.assertEqual(ranking["top_n"], 2)
        self.assertEqual(
            [item["candidate_id"] for item in ranking["ranked_candidates"]],
            ["podcast-feature", "tech-feature"],
        )
        self.assertEqual(ranking["ranked_candidates"][0]["metadata"], {"slot": "hero"})
        self.assertIn("topic-affinity-strong", ranking["ranked_candidates"][0]["reason_codes"])
        self.assertIn("topic-affinity-none", ranking["ranked_candidates"][1]["reason_codes"])
        self.assertEqual(
            sorted(ranking["ranked_candidates"][0]["breakdown"].keys()),
            ["feedback_affinity", "freshness", "site_score", "topic_affinity"],
        )
        self.assertNotIn("topics", ranking)
        self.assertNotIn("granted_scopes", ranking)
        ranking_json = json.dumps(ranking, sort_keys=True)
        self.assertNotIn("orf:media/podcasts", ranking_json)
        self.assertNotIn("orf:technology/python", ranking_json)
        self.assertNotIn("orf:health/sleep", ranking_json)
        self.assertNotIn('"weight"', ranking_json)

    def test_grant_session_feedback_changes_future_reranks_without_leaking_behavior(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.selective:orf:media/podcasts"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = self.client.post(
            f"/site-access-requests/{request_id}/approve",
            json={"approved_scopes": ["topics.selective:orf:media/podcasts"]},
        )
        self.assertEqual(approve_response.status_code, 200)

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()

        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": sign_payload(
                    exchange_body["challenge_payload"],
                    self.private_key,
                ),
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        session_id = verify_response.json()["session"]["session_id"]

        candidate_payload = {
            "schema_version": "0.3.0",
            "top_n": 2,
            "include_debug": True,
            "candidates": [
                {
                    "candidate_id": "podcast-feature",
                    "site_score": 0.55,
                    "candidate_topics": ["orf:media/podcasts"],
                },
                {
                    "candidate_id": "tech-feature",
                    "site_score": 0.81,
                    "candidate_topics": ["orf:technology/python"],
                },
            ],
        }
        initial_rank_response = self.client.post(
            f"/grant-sessions/{session_id}/rank",
            json=candidate_payload,
        )
        self.assertEqual(initial_rank_response.status_code, 200)
        self.assertEqual(
            [
                item["candidate_id"]
                for item in initial_rank_response.json()["ranking"]["ranked_candidates"]
            ],
            ["podcast-feature", "tech-feature"],
        )

        feedback_response = self.client.post(
            f"/grant-sessions/{session_id}/rank/feedback",
            json={
                "schema_version": "0.3.0",
                "events": [
                    {
                        "event_id": "feedback-1",
                        "event_type": "dismiss",
                        "candidate_id": "podcast-feature",
                        "candidate_topics": ["orf:media/podcasts"],
                        "occurred_at": "2025-01-21T10:00:00+00:00",
                    },
                    {
                        "event_id": "feedback-2",
                        "event_type": "save",
                        "candidate_id": "tech-feature",
                        "candidate_topics": ["orf:technology/python"],
                        "occurred_at": "2025-01-21T10:01:00+00:00",
                    },
                ],
            },
        )
        self.assertEqual(feedback_response.status_code, 200)
        feedback_body = feedback_response.json()["feedback"]
        self.assertEqual(feedback_body["submitted_events"], 2)
        self.assertEqual(feedback_body["accepted_events"], 2)

        duplicate_feedback_response = self.client.post(
            f"/grant-sessions/{session_id}/rank/feedback",
            json={
                "schema_version": "0.3.0",
                "events": [
                    {
                        "event_id": "feedback-1",
                        "event_type": "dismiss",
                        "candidate_id": "podcast-feature",
                        "candidate_topics": ["orf:media/podcasts"],
                        "occurred_at": "2025-01-21T10:00:00+00:00",
                    }
                ],
            },
        )
        self.assertEqual(duplicate_feedback_response.status_code, 200)
        self.assertEqual(
            duplicate_feedback_response.json()["feedback"]["accepted_events"],
            0,
        )

        rerank_response = self.client.post(
            f"/grant-sessions/{session_id}/rank",
            json=candidate_payload,
        )
        self.assertEqual(rerank_response.status_code, 200)
        reranked_candidates = rerank_response.json()["ranking"]["ranked_candidates"]
        self.assertEqual(
            [item["candidate_id"] for item in reranked_candidates],
            ["tech-feature", "podcast-feature"],
        )
        self.assertIn("feedback-positive", reranked_candidates[0]["reason_codes"])
        self.assertIn("feedback-negative", reranked_candidates[1]["reason_codes"])

        ranking_json = json.dumps(rerank_response.json()["ranking"], sort_keys=True)
        self.assertNotIn("feedback-1", ranking_json)
        self.assertNotIn("feedback-2", ranking_json)
        self.assertNotIn("orf:media/podcasts", ranking_json)
        self.assertNotIn("orf:technology/python", ranking_json)

        admin_client = TestClient(create_app(self.db_path, admin_token="secret-token"))
        audit_response = admin_client.get(
            f"/admin/audit-events?event_type=ranking-feedback.ingested&session_id={session_id}",
            headers={"X-Open-Recommender-Admin-Token": "secret-token"},
        )
        self.assertEqual(audit_response.status_code, 200)
        events = audit_response.json()["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(sorted(item["accepted_events"] for item in events), [0, 2])
        self.assertIn(["dismiss", "save"], [item["feedback_types"] for item in events])
        self.assertNotIn("candidate_id", json.dumps(events, sort_keys=True))

    def test_site_access_request_supports_required_and_optional_scopes(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "required_scopes": ["profile.read", "topics.public"],
                "optional_scopes": ["topics.selective:orf:media/podcasts", "unknown.scope"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        access_request = request_response.json()["access_request"]
        self.assertEqual(access_request["required_scopes"], ["profile.read", "topics.public"])
        self.assertEqual(
            access_request["optional_scopes"],
            ["topics.selective:orf:media/podcasts"],
        )
        self.assertEqual(
            access_request["requested_scopes"],
            ["profile.read", "topics.public", "topics.selective:orf:media/podcasts"],
        )
        self.assertEqual(access_request["ignored_requested_scopes"], ["unknown.scope"])

    def test_site_access_request_approval_requires_required_scopes(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "required_scopes": ["profile.read"],
                "optional_scopes": ["topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        missing_required_response = self.client.post(
            f"/site-access-requests/{request_id}/approve",
            json={"approved_scopes": ["topics.public"]},
        )
        self.assertEqual(missing_required_response.status_code, 400)
        self.assertIn("Required scopes cannot be removed", missing_required_response.json()["detail"])

        valid_response = self.client.post(
            f"/site-access-requests/{request_id}/approve",
            json={"approved_scopes": ["profile.read"]},
        )
        self.assertEqual(valid_response.status_code, 200)
        self.assertEqual(valid_response.json()["grant"]["approved_scopes"], ["profile.read"])

    def test_site_access_verify_accepts_unpadded_base64url_signature(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = self.client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approve_response.status_code, 200)

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()

        browser_style_signature = sign_payload(exchange_body["challenge_payload"], self.private_key).rstrip("=")
        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": browser_style_signature,
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertTrue(verify_response.json()["verified"])

    def test_site_access_request_denial_blocks_exchange(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        request_id = request_response.json()["access_request"]["request_id"]

        deny_response = self.client.post(
            f"/site-access-requests/{request_id}/deny",
            json={"reason": "User declined this pilot request."},
        )
        self.assertEqual(deny_response.status_code, 200)
        self.assertEqual(deny_response.json()["access_request"]["status"], "denied")

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 400)
        self.assertIn("not approved", exchange_response.json()["detail"])

    def test_site_access_request_expiry_blocks_approval_and_exchange(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
                "expires_at": "2000-01-01T00:00:00+00:00",
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        inspect_response = self.client.get(f"/site-access-requests/{request_id}")
        self.assertEqual(inspect_response.status_code, 200)
        self.assertEqual(inspect_response.json()["access_request"]["status"], "expired")

        approve_response = self.client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approve_response.status_code, 400)
        self.assertIn("Only pending requests", approve_response.json()["detail"])

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 400)
        self.assertIn("not approved", exchange_response.json()["detail"])

    def test_site_access_exchange_challenge_expiry_blocks_verify(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = self.client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approve_response.status_code, 200)

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()
        expired_at = "2000-01-01T00:00:00+00:00"
        self.update_challenge_created_at(exchange_body["challenge"]["challenge_id"], expired_at)

        expired_payload = dict(exchange_body["challenge_payload"])
        expired_payload["created_at"] = expired_at
        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": sign_payload(expired_payload, self.private_key),
            },
        )
        self.assertEqual(verify_response.status_code, 400)
        self.assertIn("expired", verify_response.json()["detail"])

    def test_grant_session_expiry_blocks_projection_reads(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = self.client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approve_response.status_code, 200)

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()

        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": sign_payload(exchange_body["challenge_payload"], self.private_key),
                "session_expires_at": "2000-01-01T00:00:00+00:00",
            },
        )
        self.assertEqual(verify_response.status_code, 200)

        session_id = verify_response.json()["session"]["session_id"]
        projection_response = self.client.get(f"/grant-sessions/{session_id}/projection")
        self.assertEqual(projection_response.status_code, 400)
        self.assertIn("expired", projection_response.json()["detail"])

    def test_rate_limit_blocks_repeated_challenge_requests(self) -> None:
        limited_app = create_app(
            Path(self.temp_dir.name) / "limited.db",
            rate_limit_window_seconds=60,
            rate_limit_max_requests=2,
        )
        limited_client = TestClient(limited_app)
        limited_client.post("/profiles", json={"profile": self.profile.to_document()})

        first_response = limited_client.post(f"/profiles/{self.profile.profile_id}/challenges")
        second_response = limited_client.post(f"/profiles/{self.profile.profile_id}/challenges")
        third_response = limited_client.post(f"/profiles/{self.profile.profile_id}/challenges")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(third_response.status_code, 429)
        self.assertEqual(third_response.json()["detail"]["bucket"], "profile-challenge")
        self.assertGreaterEqual(third_response.json()["detail"]["retry_after_seconds"], 1)

    def test_pilot_site_config_path_allows_custom_site_id(self) -> None:
        pilot_sites_path = Path(self.temp_dir.name) / "pilot-sites.json"
        pilot_sites_path.write_text(
            json.dumps(
                [
                    {
                        "site_id": "partner-news-demo",
                        "site_name": "Partner News Demo",
                        "allowed_scopes": ["profile.read", "topics.public", "consent.summary"],
                        "allow_selective_topics": True,
                    }
                ],
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        custom_app = create_app(
            Path(self.temp_dir.name) / "custom-sites.db",
            pilot_sites_path=pilot_sites_path,
        )
        custom_client = TestClient(custom_app)
        custom_client.post("/profiles", json={"profile": self.profile.to_document()})

        health_response = custom_client.get("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["service"]["pilot_sites_count"], 1)
        self.assertEqual(
            health_response.json()["service"]["pilot_sites_path"],
            str(pilot_sites_path),
        )

        request_response = custom_client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "partner-news-demo",
                "purpose": "Personalize partner feed.",
                "requested_scopes": ["profile.read", "topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        self.assertEqual(request_response.json()["access_request"]["site_name"], "Partner News Demo")

        unknown_site_response = custom_client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Should fail on custom config.",
                "requested_scopes": ["topics.public"],
            },
        )
        self.assertEqual(unknown_site_response.status_code, 404)

    def test_invalid_pilot_sites_path_config_raises_value_error(self) -> None:
        invalid_path = Path(self.temp_dir.name) / "invalid-pilot-sites.json"
        invalid_path.write_text("{\"not\": \"a-list\"}", encoding="utf-8")
        with self.assertRaises(ValueError):
            create_app(
                Path(self.temp_dir.name) / "invalid-sites.db",
                pilot_sites_path=invalid_path,
            )

    def test_browser_consent_review_page_and_approve_flow(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
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
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]
        self.assertIn("/consent/site-access-requests/", request_response.json()["consent_review_url"])

        inbox_response = self.client.get("/consent")
        self.assertEqual(inbox_response.status_code, 200)
        self.assertIn("Consent Inbox", inbox_response.text)
        self.assertIn(request_id, inbox_response.text)

        review_response = self.client.get(f"/consent/site-access-requests/{request_id}")
        self.assertEqual(review_response.status_code, 200)
        self.assertIn("Review site access request", review_response.text)
        self.assertIn("Open News Demo", review_response.text)
        self.assertIn("Required to continue", review_response.text)
        self.assertIn("Technology / Python", review_response.text)
        self.assertIn("Optional newly shared with this site only", review_response.text)
        token_match = re.search(r'const csrfToken = "([^"]+)"', review_response.text)
        self.assertIsNotNone(token_match)
        csrf_token = token_match.group(1)

        second_review_response = self.client.get(f"/consent/site-access-requests/{request_id}")
        second_token_match = re.search(r'const csrfToken = "([^"]+)"', second_review_response.text)
        self.assertIsNotNone(second_token_match)
        self.assertEqual(csrf_token, second_token_match.group(1))

        approve_response = self.client.post(
            f"/consent/site-access-requests/{request_id}/approve",
            headers={"X-Open-Recommender-CSRF-Token": csrf_token},
            json={
                "approved_scopes": [
                    "profile.read",
                    "topics.public",
                    "topics.selective:orf:media/podcasts",
                ]
            },
        )
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["access_request"]["status"], "approved")
        self.assertEqual(approve_response.json()["grant"]["approved_scopes"][2], "topics.selective:orf:media/podcasts")

    def test_browser_consent_deny_requires_csrf_token(self) -> None:
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        request_id = request_response.json()["access_request"]["request_id"]

        missing_token_response = self.client.post(
            f"/consent/site-access-requests/{request_id}/deny",
            json={"reason": "No thanks."},
        )
        self.assertEqual(missing_token_response.status_code, 403)

        review_response = self.client.get(f"/consent/site-access-requests/{request_id}")
        token_match = re.search(r'const csrfToken = "([^"]+)"', review_response.text)
        self.assertIsNotNone(token_match)
        csrf_token = token_match.group(1)

        deny_response = self.client.post(
            f"/consent/site-access-requests/{request_id}/deny",
            headers={"X-Open-Recommender-CSRF-Token": csrf_token},
            json={"reason": "No thanks."},
        )
        self.assertEqual(deny_response.status_code, 200)
        self.assertEqual(deny_response.json()["access_request"]["status"], "denied")

    def test_profile_lens_routes_expose_local_trust_surface(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        lens_page = self.client.get("/lens")
        self.assertEqual(lens_page.status_code, 200)
        self.assertIn("Local Profile Lens", lens_page.text)
        self.assertIn("Open a local .orf file", lens_page.text)
        self.assertIn("Register or update in local service", lens_page.text)
        self.assertIn("Review pending site requests", lens_page.text)

        list_response = self.client.get("/lens/profiles")
        self.assertEqual(list_response.status_code, 200)
        profiles = list_response.json()["profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["profile_id"], self.profile.profile_id)

        stored_response = self.client.get(f"/lens/profiles/{self.profile.profile_id}")
        self.assertEqual(stored_response.status_code, 200)
        self.assertEqual(stored_response.json()["profile"]["profile_id"], self.profile.profile_id)
        self.assertEqual(stored_response.json()["profile"]["topics"][0]["topic"], "orf:technology/python")
        self.assertNotIn("event_log", stored_response.json()["profile"])

        pending_requests_response = self.client.get(
            f"/lens/profiles/{self.profile.profile_id}/pending-requests"
        )
        self.assertEqual(pending_requests_response.status_code, 200)
        self.assertEqual(pending_requests_response.json()["requests"][0]["request_id"], request_id)

    def test_profile_lens_can_import_local_profile_into_service(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )

        import_response = self.client.post(
            "/lens/profiles/import",
            json={"profile": self.profile.to_document()},
        )
        self.assertEqual(import_response.status_code, 200)
        self.assertEqual(
            import_response.json()["profile"]["profile_id"],
            self.profile.profile_id,
        )
        self.assertEqual(
            import_response.json()["profile"]["topics"][0]["topic"],
            "orf:technology/python",
        )

        list_response = self.client.get("/lens/profiles")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["profiles"][0]["profile_id"], self.profile.profile_id)

        public_response = self.client.get(f"/profiles/{self.profile.profile_id}/public")
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response.json()["profile_id"], self.profile.profile_id)

    def test_consent_review_data_route_returns_json_for_browser_ui(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "required_scopes": ["profile.read", "topics.public"],
                "optional_scopes": ["topics.selective:orf:media/podcasts"],
            },
        )
        request_id = request_response.json()["access_request"]["request_id"]

        review_data = self.client.get(f"/consent/site-access-requests/{request_id}/review-data")
        self.assertEqual(review_data.status_code, 200)
        body = review_data.json()
        self.assertEqual(body["access_request"]["request_id"], request_id)
        self.assertIn("csrf_token", body)
        self.assertEqual(body["scope_groups"]["required"][0]["scope"], "profile.read")
        self.assertTrue(body["scope_groups"]["required"][0]["required"])
        self.assertEqual(
            body["scope_groups"]["optional_newly_shared"][0]["scope"],
            "topics.selective:orf:media/podcasts",
        )
        self.assertEqual(body["projection_preview"]["display_name"], "Alice")

    def test_create_access_request_reuses_active_grant_for_same_purpose_and_scopes(self) -> None:
        self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

        first_request = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["profile.read", "topics.public"],
            },
        )
        self.assertEqual(first_request.status_code, 200)
        first_request_id = first_request.json()["access_request"]["request_id"]

        approve_response = self.client.post(
            f"/site-access-requests/{first_request_id}/approve",
            json={"approved_scopes": ["profile.read", "topics.public"]},
        )
        self.assertEqual(approve_response.status_code, 200)
        original_grant_id = approve_response.json()["grant"]["grant_id"]

        second_request = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["profile.read", "topics.public"],
            },
        )
        self.assertEqual(second_request.status_code, 200)
        second_body = second_request.json()
        self.assertEqual(second_body["access_request"]["status"], "approved")
        self.assertEqual(second_body["access_request"]["decision_actor"], "stored-grant-reuse")
        self.assertEqual(second_body["access_request"]["reused_prior_grant_id"], original_grant_id)

    def test_grant_revocation_page_blocks_future_exchange_and_records_audit(self) -> None:
        admin_app = create_app(Path(self.temp_dir.name) / "revoke.db", admin_token="secret-token")
        admin_client = TestClient(admin_app)
        admin_client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = admin_client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["profile.read", "topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        approve_response = admin_client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approve_response.status_code, 200)
        grant_id = approve_response.json()["grant"]["grant_id"]

        grants_page = admin_client.get("/consent/grants")
        self.assertEqual(grants_page.status_code, 200)
        self.assertIn("Site Grants", grants_page.text)
        self.assertIn("try {", grants_page.text)
        self.assertIn("catch (error)", grants_page.text)
        self.assertIn("Grant revoke failed with HTTP", grants_page.text)
        token_match = re.search(
            rf"data-grant-id='{re.escape(grant_id)}' data-csrf-token='([^']+)'",
            grants_page.text,
        )
        self.assertIsNotNone(token_match)
        csrf_token = token_match.group(1)

        missing_token_response = admin_client.post(
            f"/consent/grants/{grant_id}/revoke",
            json={"reason": "No longer needed."},
        )
        self.assertEqual(missing_token_response.status_code, 403)

        revoke_response = admin_client.post(
            f"/consent/grants/{grant_id}/revoke",
            headers={"X-Open-Recommender-CSRF-Token": csrf_token},
            json={"reason": "User revoked site access."},
        )
        self.assertEqual(revoke_response.status_code, 200)
        self.assertIn("revoked_at", revoke_response.json()["grant"])

        exchange_response = admin_client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 400)
        self.assertIn("revoked", exchange_response.json()["detail"])

        audit_response = admin_client.get(
            "/admin/audit-events?event_type=grant.revoked",
            headers={"X-Open-Recommender-Admin-Token": "secret-token"},
        )
        self.assertEqual(audit_response.status_code, 200)
        self.assertEqual(audit_response.json()["events"][0]["grant_id"], grant_id)

    def test_admin_endpoints_require_token_and_return_audit_data(self) -> None:
        admin_app = create_app(Path(self.temp_dir.name) / "admin.db", admin_token="secret-token")
        admin_client = TestClient(admin_app)
        admin_client.post("/profiles", json={"profile": self.profile.to_document()})

        request_response = admin_client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        no_token_response = admin_client.get("/admin/pilot-sites")
        self.assertEqual(no_token_response.status_code, 403)

        headers = {"X-Open-Recommender-Admin-Token": "secret-token"}
        sites_response = admin_client.get("/admin/pilot-sites", headers=headers)
        self.assertEqual(sites_response.status_code, 200)
        self.assertEqual(sites_response.json()["sites"][0]["site_id"], "open-news-demo")

        audit_response = admin_client.get(
            f"/admin/audit-events?request_id={request_id}",
            headers=headers,
        )
        self.assertEqual(audit_response.status_code, 200)
        events = audit_response.json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "access-request.created")
        self.assertEqual(events[0]["request_id"], request_id)


class SyncTokenGateTests(unittest.TestCase):
    """Verify that hosted sync endpoints enforce the token when configured."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sync.db"
        self.sync_token = "test-sync-secret"
        self.app = create_app(self.db_path, sync_token=self.sync_token)
        self.client = TestClient(self.app)
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("SyncUser", public_key, "device-sync")
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_reports_sync_auth_required(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["service"]["sync_auth_required"])

    def test_pull_events_blocked_without_token(self) -> None:
        response = self.client.get(f"/profiles/{self.profile.profile_id}/events")
        self.assertEqual(response.status_code, 401)

    def test_pull_events_allowed_with_valid_token(self) -> None:
        response = self.client.get(
            f"/profiles/{self.profile.profile_id}/events",
            headers={"Authorization": f"Bearer {self.sync_token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("events", response.json())

    def test_push_events_blocked_without_token(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile.profile_id}/events",
            json={"events": []},
        )
        self.assertEqual(response.status_code, 401)

    def test_push_events_allowed_with_valid_token(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile.profile_id}/events",
            json={"events": []},
            headers={"Authorization": f"Bearer {self.sync_token}"},
        )
        self.assertEqual(response.status_code, 200)

    def test_events_open_when_no_sync_token_configured(self) -> None:
        open_app = create_app(self.db_path)
        open_client = TestClient(open_app)
        open_client.post("/profiles", json={"profile": self.profile.to_document()})
        response = open_client.get(f"/profiles/{self.profile.profile_id}/events")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(open_client.get("/health").json()["service"]["sync_auth_required"])


if __name__ == "__main__":
    unittest.main()
