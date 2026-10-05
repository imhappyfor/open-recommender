from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from open_recommender.cli import signed_owner_payload
from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import create_app
from owner_helpers import owner_post


class OwnerActionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "owners.db"
        self.app = create_app(self.db_path, rate_limit_max_requests=1000)
        self.client = TestClient(self.app)
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        event = build_registration_event(self.profile)
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        self.profile.apply_event(event)
        self.client.post("/profiles", json={"profile": self.profile.to_document()})

    def request(self):
        response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={"site_id": "open-news-demo", "purpose": "Test", "required_scopes": ["profile.read"],
                  "optional_scopes": ["topics.public"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_request"]["request_id"]

    def payload(self, action, target, parameters=None, key=None):
        def sender(method, url, body):
            response = self.client.request(method, url, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        return signed_owner_payload(
            "http://testserver", self.profile.profile_id, action, target, parameters or {}, key or self.key,
            sender=sender,
        )

    def test_unsigned_decisions_are_rejected_even_with_browser_review_token(self):
        request_id = self.request()
        review = self.client.get(f"/consent/site-access-requests/{request_id}/review-data").json()
        for action in ("approve", "deny"):
            for prefix in ("", "/consent"):
                response = self.client.post(
                    f"{prefix}/site-access-requests/{request_id}/{action}", json={},
                    headers={"X-Open-Recommender-CSRF-Token": review["csrf_token"]},
                )
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.app.state.store.get_access_request(request_id)[1].status.value, "pending")

    def test_proof_binds_action_target_parameters_and_owner(self):
        request_id = self.request()
        parameters = {"approved_scopes": ["profile.read"]}
        payload = self.payload("approve", request_id, parameters)
        path = f"/site-access-requests/{request_id}/approve"
        changed = {**payload, "approved_scopes": ["profile.read", "topics.public"]}
        self.assertEqual(self.client.post(path, json=changed).status_code, 403)
        self.assertEqual(self.client.post(f"/site-access-requests/{request_id}/deny", json={
            "owner_proof": payload["owner_proof"],
        }).status_code, 403)
        other_request = self.request()
        self.assertEqual(self.client.post(
            f"/site-access-requests/{other_request}/approve", json=payload,
        ).status_code, 403)
        wrong_key, _ = generate_key_pair()
        wrong = self.payload("approve", request_id, parameters, key=wrong_key)
        self.assertEqual(self.client.post(path, json=wrong).status_code, 403)
        self.assertEqual(self.client.post(path, json=payload).status_code, 200)
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)

    def test_expired_and_generic_challenges_cannot_authorize_decisions(self):
        request_id = self.request()
        payload = self.payload("deny", request_id)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE challenges SET created_at = ? WHERE challenge_id = ?",
                               ("2000-01-01T00:00:00+00:00", payload["owner_proof"]["challenge_id"]))
        path = f"/site-access-requests/{request_id}/deny"
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)
        challenge = self.client.post(f"/profiles/{self.profile.profile_id}/challenges").json()
        generic = {"owner_proof": {"challenge_id": challenge["challenge_id"],
                                  "signature": sign_payload(challenge, self.key)}}
        self.assertEqual(self.client.post(path, json=generic).status_code, 403)

    def test_private_history_requires_proof_bound_to_owner_cursor_and_purpose(self):
        event = build_signed_event(self.profile, EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.8, "visibility": "private"}, signature="")
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        self.app.state.store.append_events(self.profile.profile_id, [event])
        path = f"/profiles/{self.profile.profile_id}/events/read"
        payload = self.payload("sync-read", self.profile.profile_id, {"after_clock": 0})
        self.assertEqual(self.client.post(path, json={"after_clock": 0}).status_code, 401)
        self.assertEqual(self.client.post(path, json={**payload, "after_clock": 1}).status_code, 403)
        wrong_key, wrong_public = generate_key_pair()
        wrong = self.payload("sync-read", self.profile.profile_id, {"after_clock": 0}, key=wrong_key)
        self.assertEqual(self.client.post(path, json=wrong).status_code, 403)
        other = ORFProfile.create("Bob", wrong_public, "device-b")
        registration = build_registration_event(other)
        registration.signature = sign_payload(registration.unsigned_payload(), wrong_key)
        other.apply_event(registration)
        self.client.post("/profiles", json={"profile": other.to_document()})
        self.assertEqual(self.client.post(f"/profiles/{other.profile_id}/events/read", json=payload).status_code, 403)
        generic = self.client.post(f"/profiles/{self.profile.profile_id}/challenges").json()
        self.assertEqual(self.client.post(path, json={"after_clock": 0, "owner_proof": {
            "challenge_id": generic["challenge_id"], "signature": sign_payload(generic, self.key),
        }}).status_code, 403)
        self.assertEqual(self.client.post(f"/profiles/{self.profile.profile_id}/delete", json={
            "owner_proof": payload["owner_proof"],
        }).status_code, 403)
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["events"], [event.to_dict()])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)
        filtered = owner_post(self.client, self.profile, self.key, path, json={"after_clock": 1})
        self.assertEqual(filtered.json()["events"], [])

    def test_personal_surfaces_disable_caching_without_disabling_public_projection_policy(self):
        request_id = self.request()
        for path in ("/lens", "/consent", f"/site-access-requests/{request_id}"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotEqual(self.client.get(f"/profiles/{self.profile.profile_id}/public").headers.get("cache-control"), "no-store")

    def test_sync_read_challenge_expiry_validation_and_concurrent_replay(self):
        path = f"/profiles/{self.profile.profile_id}/events/read"
        for clock in (-1, 0.0, True, None, "0", 2**53):
            response = self.client.post(f"/profiles/{self.profile.profile_id}/owner-action-challenges", json={
                "action": "sync-read", "target_id": self.profile.profile_id, "parameters": {"after_clock": clock},
            })
            self.assertEqual(response.status_code, 400, response.text)
        expired = self.payload("sync-read", self.profile.profile_id, {"after_clock": 0})
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE challenges SET created_at = ? WHERE challenge_id = ?",
                ("2000-01-01T00:00:00+00:00", expired["owner_proof"]["challenge_id"]))
        self.assertEqual(self.client.post(path, json=expired).status_code, 403)
        body = self.payload("sync-read", self.profile.profile_id, {"after_clock": 0})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(path, json=body), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 403])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'sync.read'").fetchone()[0], 1)

    def test_empty_scopes_fail_without_consuming_proof(self):
        request_id = self.request()
        payload = self.payload("approve", request_id, {"approved_scopes": []})
        response = self.client.post(f"/site-access-requests/{request_id}/approve", json=payload)
        self.assertEqual(response.status_code, 400)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT used FROM challenges WHERE challenge_id = ?",
                (payload["owner_proof"]["challenge_id"],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 0)

    def test_concurrent_replay_changes_request_once(self):
        request_id = self.request()
        body = self.payload("deny", request_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.client.post, f"/site-access-requests/{request_id}/deny", json=body)
                       for _ in range(2)]
            self.assertEqual(sorted(future.result().status_code for future in futures), [200, 403])

    def test_expired_and_revoked_grants_block_existing_sessions(self):
        request_id = self.request()
        approved = owner_post(self.client, self.profile, self.key, f"/site-access-requests/{request_id}/approve")
        grant = approved.json()["grant"]
        exchange = self.client.post(f"/site-access-requests/{request_id}/exchange").json()
        verified = self.client.post(f"/site-access-requests/{request_id}/verify", json={
            "challenge_id": exchange["challenge"]["challenge_id"],
            "signature": sign_payload(exchange["challenge_payload"], self.key),
        }).json()
        session_id = verified["session"]["session_id"]
        self.assertEqual(self.client.get(f"/grant-sessions/{session_id}/projection").status_code, 200)
        grant["expires_at"] = "2000-01-01T00:00:00+00:00"
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE grants SET grant_json = ?, expires_at = ? WHERE grant_id = ?",
                (json.dumps(grant), grant["expires_at"], grant["grant_id"]))
        for state in ("expired", "revoked"):
            if state == "revoked":
                response = owner_post(self.client, self.profile, self.key, f"/grants/{grant['grant_id']}/revoke")
                self.assertEqual(response.status_code, 200, response.text)
            for method, suffix, body in (
                ("GET", "projection", None),
                ("POST", "rank", {"candidates": [{"candidate_id": "story-one", "topics": ["science"]}]}),
                ("POST", "rank/feedback", {"events": [{"event_id": "feedback-revoked", "event_type": "click", "candidate_id": "story-one"}]}),
            ):
                with self.subTest(state=state, route=suffix):
                    response = self.client.request(method, f"/grant-sessions/{session_id}/{suffix}", json=body)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn(state, response.json()["detail"])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ranking_feedback_events").fetchone()[0], 0)

    def test_delete_removes_all_profile_rows_and_preserves_other_profiles(self):
        request_id = self.request()
        approved = owner_post(self.client, self.profile, self.key, f"/site-access-requests/{request_id}/approve")
        grant_id = approved.json()["grant"]["grant_id"]
        exchange = self.client.post(f"/site-access-requests/{request_id}/exchange").json()
        verified = self.client.post(f"/site-access-requests/{request_id}/verify", json={
            "challenge_id": exchange["challenge"]["challenge_id"],
            "signature": sign_payload(exchange["challenge_payload"], self.key),
        }).json()
        session_id = verified["session"]["session_id"]
        response = self.client.post(f"/grant-sessions/{session_id}/rank/feedback", json={"events": [{
            "event_id": "feedback-delete", "event_type": "click", "candidate_id": "story-one",
        }]})
        self.assertEqual(response.status_code, 200, response.text)
        other_key, other_public = generate_key_pair()
        other = ORFProfile.create("Bob", other_public, "device-b")
        event = build_registration_event(other)
        event.signature = sign_payload(event.unsigned_payload(), other_key)
        other.apply_event(event)
        self.client.post("/profiles", json={"profile": other.to_document()})
        path = f"/profiles/{self.profile.profile_id}/delete"
        self.assertEqual(self.client.post(path, json={}).status_code, 401)
        body = self.payload("delete", self.profile.profile_id)
        self.assertEqual(self.client.post(f"/profiles/{other.profile_id}/delete", json=body).status_code, 403)
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        removed = response.json()["removed_rows"]
        for table, count in removed.items():
            self.assertGreater(count, 0, table)
            with sqlite3.connect(self.db_path) as connection:
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?",
                    (self.profile.profile_id,)).fetchone()[0], 0)
        self.assertIsNotNone(self.app.state.store.get_profile(other.profile_id))
        self.assertEqual(self.client.get(f"/grant-sessions/{session_id}/projection").status_code, 404)
        self.assertEqual(self.client.post(f"/grants/{grant_id}/revoke", json=body).status_code, 404)

    def test_deletion_error_rolls_back_all_rows_and_proof_consumption(self):
        self.request()
        payload = self.payload("delete", self.profile.profile_id)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TRIGGER stop_delete BEFORE DELETE ON profiles BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(f"/profiles/{self.profile.profile_id}/delete", json=payload)
        with sqlite3.connect(self.db_path) as connection:
            for table in ("profiles", "events", "access_requests", "audit_events"):
                self.assertGreater(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT used FROM challenges WHERE challenge_id = ?",
                (payload["owner_proof"]["challenge_id"],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
