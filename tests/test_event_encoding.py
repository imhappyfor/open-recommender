from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from open_recommender.crypto import (
    canonical_signed_json, generate_key_pair, load_json, sign_payload, verify_signature,
)
from open_recommender.models import (
    EventOp, ORFProfile, SignedEvent, build_registration_event, build_signed_event,
)
from open_recommender.service import create_app


class EventEncodingTests(unittest.TestCase):
    def test_json_text_rejects_duplicate_fields_before_signature_interpretation(self):
        for raw in ('{"value":1,"value":2}', '{"nested":{"value":1,"value":2}}',
            '{"signature_encoding":"jcs-rfc8785","signature_encoding":null}', '{"value":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_json(raw)
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory) / "duplicates.db")) as client:
            raw = '{"profile":{},"profile":{}}'
            for headers in ({}, {"Content-Type": "application/json"}, {"Content-Type": "application/profile+json"}):
                response = client.post("/profiles", content=raw, headers=headers)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("unique object fields", response.text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_shared_vectors_survive_javascript_number_and_unicode_encoding(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/event-signing-vectors.json").read_text())
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        registration = fixture["vectors"][0]["payload"]
        profile = ORFProfile.from_document({
            "schema_version": "0.2.0", "profile_id": fixture["profile_id"],
            "public_key": fixture["public_key"], "display_name": "Vector user",
            "created_at": registration["timestamp"], "updated_at": registration["timestamp"],
        })
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory) / "events.db")) as client:
            for vector in fixture["vectors"]:
                with self.subTest(vector=vector["name"]):
                    original = vector["payload"]
                    wire = json.loads(vector["canonical_utf8"])
                    self.assertEqual(canonical_signed_json(original), vector["canonical_utf8"].encode())
                    self.assertEqual(canonical_signed_json(wire), vector["canonical_utf8"].encode())
                    self.assertEqual(sign_payload(wire, key), vector["signature"])
                    self.assertTrue(verify_signature(wire, vector["signature"], fixture["public_key"]))
                    event = SignedEvent.from_dict({**wire, "signature": vector["signature"]})
                    profile.apply_event(event)
                    if event.clock == 0:
                        response = client.post("/profiles", json={"profile": profile.to_document()})
                    else:
                        response = client.post(f"/profiles/{profile.profile_id}/events", json={"events": [event.to_dict()]})
                    self.assertEqual(response.status_code, 200, response.text)
                    if event.op == EventOp.SET_TOPIC:
                        self.assertEqual(response.json()["public_profile"]["topics"], [])
            rebuilt = ORFProfile.from_document(profile.to_document()).rebuild_from_verified_history()
            self.assertEqual(rebuilt.display_name, "Vector ☕")
            self.assertFalse(rebuilt.consent.ad_personalization)
            self.assertIn("orf:media/podcasts", rebuilt.opt_out_topics)
            self.assertEqual(len(rebuilt.event_log), 7)
            retry = {**fixture["vectors"][1]["payload"], "signature": fixture["vectors"][1]["signature"]}
            retry["payload"] = {**retry["payload"], "weight": 1}
            response = client.post(f"/profiles/{profile.profile_id}/events", json={"events": [retry]})
            self.assertEqual(response.status_code, 200, response.text)

    def test_legacy_history_stays_legacy_and_new_events_can_be_appended(self):
        key, public_key = generate_key_pair()
        profile = ORFProfile.create("Legacy", public_key, "device-a")
        profile.schema_version = "0.1.0"
        registration = build_registration_event(profile)
        registration.signature_encoding = None
        registration.signature = sign_payload(registration.unsigned_payload(), key)
        profile.apply_event(registration)
        old = build_signed_event(profile, EventOp.SET_TOPIC,
            {"topic": "orf:science", "weight": 1.0, "visibility": "public"}, "", signature_encoding=None)
        old.signature = sign_payload(old.unsigned_payload(), key)
        profile.apply_event(old)
        old_document = deepcopy(profile.to_document())
        new = build_signed_event(profile, EventOp.SET_CONSENT, {"field": "ad_personalization", "value": False}, "")
        new.signature = sign_payload(new.unsigned_payload(), key)
        profile.apply_event(new)
        rebuilt = ORFProfile.from_document(profile.to_document()).rebuild_from_verified_history()
        self.assertEqual([event.to_dict() for event in rebuilt.event_log[:2]], old_document["event_log"])
        self.assertFalse(rebuilt.consent.ad_personalization)
        changed = deepcopy(old.unsigned_payload())
        changed["payload"]["weight"] = 1
        with self.assertRaises(ValueError):
            verify_signature(changed, old.signature, public_key)

    def test_encoding_marker_is_signed_and_new_profiles_reject_downgrades(self):
        key, public_key = generate_key_pair()
        profile = ORFProfile.create("Alice", public_key, "device-a")
        event = build_registration_event(profile)
        event.signature = sign_payload(event.unsigned_payload(), key)
        stripped = event.unsigned_payload()
        del stripped["signature_encoding"]
        with self.assertRaises(ValueError):
            verify_signature(stripped, event.signature, public_key)
        event.signature_encoding = None
        event.signature = sign_payload(event.unsigned_payload(), key)
        before = deepcopy(profile.to_document())
        with self.assertRaises(ValueError):
            profile.apply_event(event)
        self.assertEqual(profile.to_document(), before)
        for encoding in (None, "", "future", 1):
            data = {**event.to_dict(), "signature_encoding": encoding}
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                SignedEvent.from_dict(data)

    def test_binary64_and_unicode_domains_are_checked_before_signing(self):
        def payload(value):
            return {"signature_encoding": "jcs-rfc8785", "nested": {"value": value}}
        self.assertEqual(canonical_signed_json(payload(10**20)), canonical_signed_json(payload(1e20)))
        self.assertEqual(canonical_signed_json(payload(-0.0)), canonical_signed_json(payload(0)))
        for value in (2**53 + 1, 10**20 + 1, 10**400, float("nan"), float("inf"), "\ud800", {"\udc00": 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_signed_json(payload(value))


if __name__ == "__main__":
    unittest.main()
