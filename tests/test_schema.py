from copy import deepcopy
from importlib.resources import files
import json
import unittest

from jsonschema import Draft202012Validator, ValidationError

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import (
    EventOp, ORFProfile, SignedEvent, build_registration_event, build_signed_event,
)


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads(files("open_recommender").joinpath("schemas/profile.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        cls.profile_validator = Draft202012Validator(schema)
        cls.event_validator = cls.profile_validator.evolve(schema={"$ref": "#signedEvent"})

    def setUp(self):
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        self.profile.apply_event(registration)

    def event(self, op, payload):
        event = build_signed_event(self.profile, op, payload, signature="", clock=1)
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        return event.to_dict()

    def test_emitted_profiles_and_every_operation_conform(self):
        operations = [
            (EventOp.SET_TOPIC, {"topic": "orf:technology/python", "weight": 0.7, "visibility": "private"}),
            (EventOp.REMOVE_TOPIC, {"topic": "orf:technology/python"}),
            (EventOp.SET_CONSENT, {"field": "ad_personalization", "value": False}),
            (EventOp.SET_OPT_OUT, {"topic": "orf:technology/python", "value": True}),
            (EventOp.SET_PROFILE, {"display_name": "Alice ☕"}),
            (EventOp.RECOMMEND, {"item_id": "item-a", "site_id": "site-a"}),
        ]
        for op, payload in operations:
            with self.subTest(op=op):
                event = self.event(op, payload)
                self.event_validator.validate(event)
                self.profile.apply_event(SignedEvent.from_dict(event))
                document = self.profile.to_document()
                self.profile_validator.validate(document)
                ORFProfile.from_document(document).rebuild_from_verified_history()
        minimal = {key: value for key, value in document.items() if key in (
            "schema_version", "profile_id", "display_name", "public_key", "created_at", "updated_at"
        )}
        self.profile_validator.validate(minimal)
        ORFProfile.from_document(minimal)

    def test_invalid_event_shapes_fail_schema_and_model(self):
        valid = self.event(EventOp.SET_TOPIC, {"topic": "orf:science", "weight": 0.5, "visibility": "public"})
        mutations = [
            ("clock", True), ("clock", -1), ("clock", 2**53), ("clock", "1"),
            ("event_id", 12), ("device_id", ""), ("timestamp", None),
            ("signature", 12), ("payload", []), ("op", "unknown"),
            ("payload", {"topic": "orf:science", "weight": True, "visibility": "public"}),
            ("payload", {"topic": "orf:science", "weight": 1.1, "visibility": "public"}),
            ("payload", {"topic": "orf:science", "weight": 0.5, "visibility": "unknown"}),
            ("payload", {"topic": "orf:science", "visibility": "public"}),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                document = deepcopy(valid)
                document[field] = value
                with self.assertRaises(ValidationError):
                    self.event_validator.validate(document)
                with self.assertRaises(ValueError):
                    SignedEvent.from_dict(document)
        for op, payload in [
            ("remove_topic", {}), ("set_opt_out", {"topic": "orf:science", "value": 1}),
            ("set_consent", {"field": "unknown", "value": False}),
            ("set_profile", {"display_name": 12}),
            ("recommend", {"item_id": "a", "site_id": "b", "metadata": []}),
            ("recommend", {"item_id": "a", "site_id": "b", "score": "0.5"}),
        ]:
            with self.subTest(op=op, payload=payload):
                document = {**valid, "op": op, "payload": payload}
                with self.assertRaises(ValidationError):
                    self.event_validator.validate(document)
                with self.assertRaises(ValueError):
                    SignedEvent.from_dict(document)
        registration = self.profile.event_log[0].to_dict()
        del registration["payload"]["created_at"]
        with self.assertRaises(ValidationError):
            self.event_validator.validate(registration)

    def test_invalid_snapshot_shapes_and_unknown_fields(self):
        valid = self.profile.to_document()
        for field, value in [
            ("topics", {}), ("event_log", {}), ("opt_out_topics", {}),
            ("consent", {"ad_personalization": "false"}),
            ("sync", {"device_id": "a", "topic_clocks": {"orf:science": True}}),
            ("display_name", 12),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    self.profile_validator.validate({**valid, field: value})
                with self.assertRaises(ValueError):
                    ORFProfile.from_document({**valid, field: value})
        valid["extension"] = {"example": True}
        valid["event_log"][0]["payload"]["extension"] = {"example": True}
        valid["event_log"][0]["signature"] = sign_payload(
            SignedEvent.from_dict(valid["event_log"][0]).unsigned_payload(), self.key
        )
        self.profile_validator.validate(valid)
        rebuilt = ORFProfile.from_document(valid).rebuild_from_verified_history()
        self.assertNotIn("extension", rebuilt.to_document())
        self.assertIn("extension", rebuilt.event_log[0].payload)

    def test_schema_is_not_a_security_or_semantic_validator(self):
        valid = self.event(EventOp.SET_TOPIC, {"topic": "orf:science", "weight": 0.5, "visibility": "public"})
        for field, value in [("clock", 1.0), ("timestamp", "invalid"), ("device_id", " ")]:
            with self.subTest(field=field):
                document = {**valid, field: value}
                self.event_validator.validate(document)
                with self.assertRaises(ValueError):
                    SignedEvent.from_dict(document)
        invalid_topic = deepcopy(valid)
        invalid_topic["payload"]["topic"] = "not a topic"
        self.event_validator.validate(invalid_topic)
        with self.assertRaises(ValueError):
            SignedEvent.from_dict(invalid_topic)
        forged = self.profile.to_document()
        forged["event_log"][0]["payload"]["display_name"] = "Impersonator"
        self.profile_validator.validate(forged)
        with self.assertRaises(ValueError):
            ORFProfile.from_document(forged).rebuild_from_verified_history()
        unsigned = {**forged, "event_log": []}
        self.profile_validator.validate(unsigned)
        with self.assertRaises(ValueError):
            ORFProfile.from_document(unsigned).rebuild_from_verified_history()


if __name__ == "__main__":
    unittest.main()
