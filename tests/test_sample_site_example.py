from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, save_private_key, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.service import create_app


def load_sample_site_module():
    sample_site_path = (
        Path(__file__).resolve().parents[1] / "examples" / "sample_site.py"
    )
    spec = importlib.util.spec_from_file_location("sample_site_example", sample_site_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SampleSiteExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "service.db"
        self.orf_app = create_app(self.db_path)
        self.orf_client = TestClient(self.orf_app)
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        topic_event = build_signed_event(
            self.profile,
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            signature="",
        )
        topic_event.signature = sign_payload(topic_event.unsigned_payload(), self.private_key)
        self.profile.apply_event(topic_event)
        public_event = build_signed_event(
            self.profile,
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
            signature="",
        )
        public_event.signature = sign_payload(public_event.unsigned_payload(), self.private_key)
        self.profile.apply_event(public_event)
        register_response = self.orf_client.post("/profiles", json={"profile": self.profile.to_document()})
        self.assertEqual(register_response.status_code, 200)

        self.key_path = Path(self.temp_dir.name) / "profile.orf.key"
        save_private_key(self.key_path, self.private_key)
        self.sample_site_module = load_sample_site_module()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _send_json(self, method: str, url: str, body: dict | None = None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.orf_client.request(method, path, json=body)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def test_sample_site_creates_request_and_completes_demo_sign_in(self) -> None:
        sample_app = self.sample_site_module.create_sample_site_app(
            orf_service_url="http://testserver",
            demo_signer_key_path=self.key_path,
            send_json_fn=self._send_json,
        )
        sample_client = TestClient(sample_app)

        connect_response = sample_client.get("/connect", params={"profile_id": self.profile.profile_id})
        self.assertEqual(connect_response.status_code, 200)
        self.assertIn("Current request", connect_response.text)
        self.assertIn("Open consent review", connect_response.text)

        request_id = next(iter(sample_app.state.sessions))

        approve_response = self.orf_client.post(
            f"/site-access-requests/{request_id}/approve",
            json={
                "approved_scopes": [
                    "profile.read",
                    "topics.public",
                    "topics.selective:orf:media/podcasts",
                ],
                "actor": "test-user",
            },
        )
        self.assertEqual(approve_response.status_code, 200)

        complete_response = sample_client.post(f"/session/{request_id}/complete")
        self.assertEqual(complete_response.status_code, 200)
        self.assertIn("Localhost demo signer enabled", complete_response.text)
        self.assertIn("Personalized sample feed", complete_response.text)
        self.assertIn("orf:technology/python", complete_response.text)
        self.assertIn("orf:media/podcasts", complete_response.text)
