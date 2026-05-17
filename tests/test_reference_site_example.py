from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, save_private_key
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.service import create_app


def load_example_module():
    example_path = (
        Path(__file__).resolve().parents[1] / "examples" / "pilot_flow.py"
    )
    spec = importlib.util.spec_from_file_location("pilot_flow_example", example_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReferenceSiteExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "service.db"
        self.app = create_app(self.db_path)
        self.client = TestClient(self.app)
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        event = build_signed_event(
            self.profile,
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            signature="",
        )
        from open_recommender.crypto import sign_payload

        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        self.profile.apply_event(event)
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.profile_path = Path(self.temp_dir.name) / "profile.orf"
        self.profile_path.write_text(
            json.dumps(self.profile.to_document(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.key_path = self.profile_path.with_suffix(".orf.key")
        save_private_key(self.key_path, self.private_key)
        self.example = load_example_module()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _send_json(self, method: str, url: str, body: dict | None = None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.client.request(method, path, json=body)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def test_reference_pilot_flow_example_runs_end_to_end(self) -> None:
        self.example.send_json = self._send_json

        response = self.example.run_reference_pilot_flow(
            "http://testserver",
            profile_path=self.profile_path,
            auto_approve=True,
        )

        self.assertEqual(response["request"]["access_request"]["status"], "pending")
        self.assertEqual(response["approval"]["access_request"]["status"], "approved")
        self.assertTrue(response["verify"]["verified"])
        self.assertEqual(
            [topic["topic"] for topic in response["projection"]["projection"]["topics"]],
            ["orf:media/podcasts"],
        )
