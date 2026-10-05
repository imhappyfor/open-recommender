import copy
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from open_recommender.crypto import canonical_json, encode_bytes, generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import create_app
from owner_helpers import owner_post


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = create_app(Path(self.temp_dir.name) / "validation.db", rate_limit_max_requests=500)
        self.client = TestClient(self.app)
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        self.profile.apply_event(registration)
        self.assertEqual(self.post("/profiles", {"profile": self.profile.to_document()}).status_code, 200)

    def post(self, path, body):
        # Exercise hostile raw JSON too; normal HTTP clients may refuse NaN before sending.
        return self.client.post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})

    def event(self, op=EventOp.SET_TOPIC, payload=None):
        event = build_signed_event(self.profile, op, payload if payload is not None else {
            "topic": "orf:health/sleep", "weight": 0.7, "visibility": "private",
        }, signature="")
        return event.to_dict()

    def sign_raw(self, event):
        unsigned = {key: value for key, value in event.items() if key != "signature"}
        # Deliberately bypass the safe signer to simulate an independent, broken client.
        event["signature"] = encode_bytes(self.key.sign(json.dumps(unsigned,
            sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")))
        return event

    def assert_rejected_event(self, event):
        before = self.app.state.store.get_profile(self.profile.profile_id).to_document()
        for path in ("/profiles", "/lens/profiles/import", f"/profiles/{self.profile.profile_id}/events"):
            with self.subTest(path=path):
                doc = self.profile.to_document()
                doc["event_log"].append(event)
                body = {"events": [event]} if path.endswith("/events") else {"profile": doc}
                response = self.post(path, body)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).to_document(), before)
                self.assertEqual(self.app.state.store.list_events(self.profile.profile_id), [])

    def session(self):
        request = self.post(f"/profiles/{self.profile.profile_id}/site-access-requests", {
            "site_id": "open-news-demo", "purpose": "Validation test", "required_scopes": ["profile.read"],
        }).json()["access_request"]
        request_id = request["request_id"]
        response = owner_post(self.client, self.profile, self.key,
            f"/site-access-requests/{request_id}/approve", json={"approved_scopes": ["profile.read"]})
        self.assertEqual(response.status_code, 200, response.text)
        exchange = self.post(f"/site-access-requests/{request_id}/exchange", {}).json()
        response = self.post(f"/site-access-requests/{request_id}/verify", {
            "challenge_id": exchange["challenge"]["challenge_id"],
            "signature": sign_payload(exchange["challenge_payload"], self.key),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["session"]["session_id"]

    def test_signing_rejects_non_finite_numbers_at_any_depth(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_json({"metadata": [{"value": value}]})

    def test_signed_envelopes_are_not_coerced_or_partially_applied(self):
        changes = [("clock", value) for value in (-1, True, 1.5, "1", 2**53)] + [
            ("event_id", None), ("profile_id", 12), ("device_id", []), ("timestamp", "invalid"),
            ("timestamp", "2026-01-01T00:00:00"), ("payload", []), ("op", "unknown"),
            ("timestamp", "0001-01-01T00:00:00+23:59"),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                event = self.event()
                event[field] = value
                self.assert_rejected_event(self.sign_raw(event))
        event = self.sign_raw(self.event())
        event["clock"] = 1.5  # Formerly normalized to signed integer 1 before verification.
        self.assert_rejected_event(event)
        self.assert_rejected_event({})

    def test_signed_payloads_require_finite_weights_and_real_booleans(self):
        for weight in (float("nan"), float("inf"), float("-inf"), -0.1, 1.1, True, "0.7", None):
            with self.subTest(weight=weight):
                event = self.event()
                event["payload"]["weight"] = weight
                self.assert_rejected_event(self.sign_raw(event))
        for op, payload in (
            (EventOp.SET_CONSENT, {"field": "ad_personalization", "value": "false"}),
            (EventOp.SET_CONSENT, {"field": "to_dict", "value": True}),
            (EventOp.SET_OPT_OUT, {"topic": "orf:health/sleep", "value": 0}),
            (EventOp.SET_TOPIC, {"topic": [], "weight": 0.7, "visibility": "private"}),
            (EventOp.SET_TOPIC, {"topic": "orf:health/sleep", "weight": 0.7, "visibility": None}),
            (EventOp.SET_PROFILE, {"display_name": False}),
            (EventOp.RECOMMEND, {"item_id": "story", "site_id": "site", "score": 2}),
            (EventOp.RECOMMEND, {"item_id": "story", "site_id": "site", "metadata": []}),
            (EventOp.RECOMMEND, {"item_id": "story", "site_id": "site", "metadata": {"nested": float("inf")}}),
        ):
            with self.subTest(op=op, payload=payload):
                self.assert_rejected_event(self.sign_raw(self.event(op, payload)))

    def test_bad_profile_and_batch_shapes_return_client_errors(self):
        original = self.app.state.store.get_profile(self.profile.profile_id).to_document()
        for field, value in (("sync", None), ("consent", None), ("topics", None),
            ("event_log", {}), ("opt_out_topics", "orf:health/sleep"), ("public_key", None)):
            doc = self.profile.to_document()
            doc[field] = value
            for path in ("/profiles", "/lens/profiles/import"):
                with self.subTest(field=field, path=path):
                    response = self.post(path, {"profile": doc})
                    self.assertEqual(response.status_code, 400, response.text)
        for value in (None, {}, "events", 1, [None], [{}]):
            response = self.post(f"/profiles/{self.profile.profile_id}/events", {"events": value})
            self.assertEqual(response.status_code, 400, response.text)
        response = self.post(f"/profiles/{self.profile.profile_id}/events", {"events": []})
        self.assertEqual(response.status_code, 200, response.text)
        valid = self.sign_raw(self.event())
        response = self.post(f"/profiles/{self.profile.profile_id}/events", {"events": [valid, {}]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).to_document(), original)

    def test_ranking_and_feedback_reject_bad_shapes_and_non_finite_metadata(self):
        session_id = self.session()
        candidate = {"candidate_id": "story", "site_score": 0.7}
        candidates = [None, [], {}, {"candidate_id": None, "site_score": 0.7}]
        candidates += [{**candidate, "site_score": score} for score in
            (float("nan"), float("inf"), float("-inf"), True, "0.7", -1, 2, 10**400)]
        candidates += [{**candidate, "candidate_topics": [None]},
            {**candidate, "metadata": {"nested": [float("nan")]}},
            {**candidate, "published_at": "0001-01-01T00:00:00+23:59"}]
        for item in candidates:
            with self.subTest(candidate=item):
                response = self.post(f"/grant-sessions/{session_id}/rank", {"candidates": [item]})
                self.assertEqual(response.status_code, 400, response.text)
        response = self.post(f"/grant-sessions/{session_id}/rank", {"candidates": [candidate, candidate]})
        self.assertEqual(response.status_code, 400, response.text)
        feedback = {"event_id": "feedback", "candidate_id": "story", "event_type": "click"}
        for item in (None, [], {**feedback, "event_id": None}, {**feedback, "candidate_id": {}},
            {**feedback, "candidate_topics": [12]}, {**feedback, "metadata": {"nested": float("inf")}}):
            with self.subTest(feedback=item):
                response = self.post(f"/grant-sessions/{session_id}/rank/feedback", {"events": [feedback, item]})
                self.assertEqual(response.status_code, 400, response.text)
        with self.app.state.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ranking_feedback_events").fetchone()[0], 0)

    def test_valid_boundary_numbers_still_work_and_invalid_local_events_are_atomic(self):
        for weight in (0, 1.0):
            event = self.event()
            event["payload"]["weight"] = weight
            event["signature"] = sign_payload({key: value for key, value in event.items() if key != "signature"}, self.key)
            response = self.post(f"/profiles/{self.profile.profile_id}/events", {"events": [event]})
            self.assertEqual(response.status_code, 200, response.text)
        session_id = self.session()
        response = self.post(f"/grant-sessions/{session_id}/rank", {"candidates": [
            {"candidate_id": "low", "site_score": 0}, {"candidate_id": "high", "site_score": 1.0},
        ]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ranking"]["ranked_candidates"][0]["candidate_id"], "high")
        before = copy.deepcopy(self.profile.to_document())
        invalid = build_signed_event(self.profile, EventOp.SET_CONSENT,
            {"field": "ad_personalization", "value": "false"}, signature="")
        with self.assertRaises(ValueError):
            self.profile.apply_event(invalid)
        self.assertEqual(self.profile.to_document(), before)
        invalid = build_signed_event(self.profile, EventOp.RECOMMEND, {
            "item_id": "story", "site_id": "site", "metadata": {"nested": float("nan")},
        }, signature="")
        with self.assertRaises(ValueError):
            self.profile.apply_event(invalid)
        self.assertEqual(self.profile.to_document(), before)


if __name__ == "__main__":
    unittest.main()
