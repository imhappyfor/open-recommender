from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import create_app


class ProfileIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = create_app(Path(self.temp_dir.name) / "integrity.db")
        self.client = TestClient(self.app)
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        self.profile.apply_event(registration)

    def topic_event(self, topic, *, clock=1, visibility="public"):
        event = build_signed_event(
            self.profile, EventOp.SET_TOPIC,
            {"topic": topic, "weight": 0.7, "visibility": visibility},
            signature="", clock=clock,
        )
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        return event

    def register(self):
        response = self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.assertEqual(response.status_code, 200, response.text)

    def test_unsigned_initial_registration_and_overwrite_are_rejected(self):
        unsigned = self.profile.to_document()
        unsigned["event_log"] = []
        unsigned["display_name"] = "Impersonator"
        for path in ("/profiles", "/lens/profiles/import"):
            with self.subTest(path=path):
                response = self.client.post(path, json={"profile": unsigned})
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(self.app.state.store.get_profile(self.profile.profile_id))
        self.register()
        for path in ("/profiles", "/lens/profiles/import"):
            self.assertEqual(self.client.post(path, json={"profile": unsigned}).status_code, 400)
            self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).display_name, "Alice")

    def test_hosted_state_comes_only_from_signed_events(self):
        self.profile.apply_event(self.topic_event("orf:health/sleep", visibility="private"))
        altered = self.profile.to_document()
        altered["display_name"] = "Impersonator"
        altered["created_at"] = "1999-01-01T00:00:00+00:00"
        altered["updated_at"] = "2999-01-01T00:00:00+00:00"
        altered["topics"][0]["visibility"] = "public"
        altered["consent"]["share_public_topics"] = False
        altered["sync"]["device_id"] = "forged-device"
        response = self.client.post("/profiles", json={"profile": altered})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.app.state.store.get_profile(self.profile.profile_id).to_document(),
            self.profile.to_document(),
        )
        self.assertEqual(response.json()["public_profile"]["topics"], [])

    def test_file_upload_requires_preserving_signed_numeric_representation(self):
        self.profile.schema_version = "0.1.0"
        registration = self.profile.event_log[0]
        registration.payload["schema_version"] = "0.1.0"
        registration.signature_encoding = None
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        event = self.topic_event("orf:technology/python")
        event.signature_encoding = None
        event.payload["weight"] = 1.0
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        self.profile.apply_event(event)
        raw = '{"profile":' + json.dumps(self.profile.to_document()) + '}'
        changed = raw.replace('"weight": 1.0', '"weight": 1')
        self.assertNotEqual(raw, changed)
        for path in ("/profiles", "/lens/profiles/import"):
            with self.subTest(path=path):
                valid = self.client.post(path, content=raw, headers={"Content-Type": "application/json"})
                self.assertEqual(valid.status_code, 200, valid.text)
                invalid = self.client.post(path, content=changed, headers={"Content-Type": "application/json"})
                self.assertEqual(invalid.status_code, 400, invalid.text)
                self.assertIn("Signature verification failed", invalid.text)

    def test_wrong_key_cannot_register_and_invalid_batches_roll_back(self):
        altered = self.profile.to_document()
        other_key, _ = generate_key_pair()
        altered["event_log"][0]["signature"] = sign_payload(
            self.profile.event_log[0].unsigned_payload(), other_key
        )
        self.assertEqual(self.client.post("/profiles", json={"profile": altered}).status_code, 400)
        with self.app.state.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.register()
        valid = self.topic_event("orf:technology/python")
        invalid = self.topic_event("orf:media/podcasts", clock=2)
        invalid.signature = sign_payload(invalid.unsigned_payload(), other_key)
        for path in ("/profiles", "/lens/profiles/import", f"/profiles/{self.profile.profile_id}/events"):
            with self.subTest(path=path):
                document = self.profile.to_document()
                document["event_log"].extend([valid.to_dict(), invalid.to_dict()])
                body = {"events": [valid.to_dict(), invalid.to_dict()]} if path.endswith("/events") else {"profile": document}
                self.assertEqual(self.client.post(path, json=body).status_code, 400)
                self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).topics, {})
                self.assertEqual(self.app.state.store.list_events(self.profile.profile_id), [])

    def test_stale_uploads_and_retries_preserve_later_events(self):
        stale = self.profile.to_document()
        self.register()
        event = self.topic_event("orf:media/podcasts")
        self.app.state.store.append_events(self.profile.profile_id, [event])
        before = self.app.state.store.get_profile(self.profile.profile_id).to_document()
        for path in ("/profiles", "/lens/profiles/import"):
            self.assertEqual(self.client.post(path, json={"profile": stale}).status_code, 200)
            self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).to_document(), before)
        self.app.state.store.append_events(self.profile.profile_id, [event, event])
        self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).to_document(), before)

    def test_conflicting_event_ids_and_negative_clocks_are_rejected(self):
        self.register()
        event = self.topic_event("orf:media/podcasts")
        event.payload["weight"] = 1.0
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        self.app.state.store.append_events(self.profile.profile_id, [event])
        before = self.app.state.store.get_profile(self.profile.profile_id).to_document()
        conflicting = self.topic_event("orf:technology/python", clock=2)
        conflicting.event_id = event.event_id
        conflicting.signature = sign_payload(conflicting.unsigned_payload(), self.key)
        changed_number = type(event).from_dict(event.to_dict())
        changed_number.payload["weight"] = 1
        changed_number.signature = sign_payload(changed_number.unsigned_payload(), self.key)
        negative = self.topic_event("orf:technology/python", clock=-1)
        self.app.state.store.append_events(self.profile.profile_id, [changed_number])
        for invalid in (conflicting, negative):
            with self.assertRaises(ValueError):
                self.app.state.store.append_events(self.profile.profile_id, [invalid])
        self.assertEqual(self.app.state.store.get_profile(self.profile.profile_id).to_document(), before)

    def test_concurrent_updates_preserve_both_events(self):
        self.register()
        events = [self.topic_event(topic) for topic in ("orf:media/podcasts", "orf:technology/python")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.app.state.store.append_events, self.profile.profile_id, [event]) for event in events]
            for future in futures:
                future.result()
        stored = self.app.state.store.get_profile(self.profile.profile_id)
        self.assertEqual(set(stored.topics), {"orf:media/podcasts", "orf:technology/python"})
        self.assertEqual(len(stored.event_log), 3)

    def test_legacy_hosted_upgrade_preserves_existing_event_rows(self):
        event = self.topic_event("orf:media/podcasts")
        legacy = self.profile.to_document()
        legacy["event_log"] = []
        with self.app.state.store._connect() as connection:
            connection.execute(
                "INSERT INTO profiles VALUES (?, ?, ?, ?, ?, ?)",
                (self.profile.profile_id, self.profile.public_key, "Alice", json.dumps(legacy),
                 self.profile.created_at, self.profile.updated_at),
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (event.event_id, event.profile_id, event.clock, event.timestamp,
                 json.dumps(event.to_dict())),
            )
        self.register()
        stored = self.app.state.store.get_profile(self.profile.profile_id)
        self.assertEqual(set(stored.topics), {"orf:media/podcasts"})
        self.assertEqual(len(stored.event_log), 2)


if __name__ == "__main__":
    unittest.main()
