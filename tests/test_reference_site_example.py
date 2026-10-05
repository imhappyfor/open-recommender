from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, save_private_key, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import create_app
from open_recommender.partner_sdk import PartnerSDKError


def load_example_module(name="pilot_flow"):
    example_path = (
        Path(__file__).resolve().parents[1] / "examples" / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"{name}_example", example_path)
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
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.private_key)
        self.profile.apply_event(registration)
        event = build_signed_event(
            self.profile,
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            signature="",
        )
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

    def _send_json(self, method: str, url: str, body: dict | None = None, *, extra_headers=None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.client.request(method, path, json=body, headers=extra_headers)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def test_reference_pilot_flow_example_runs_end_to_end(self) -> None:
        self.example.send_json = self._send_json

        for credentialed in (False, True):
            token = "reference-backend-test-" + "x" * 43 if credentialed else None
            self.app = create_app(self.db_path, site_token_hashes=(
                {"open-news-demo": hashlib.sha256(token.encode()).hexdigest()} if credentialed else None))
            self.client = TestClient(self.app)
            with self.subTest(credentialed=credentialed), \
                    patch("open_recommender.partner_sdk._http_send", side_effect=self._send_json):
                response = self.example.run_reference_pilot_flow(
                    "http://testserver", profile_path=self.profile_path, auto_approve=True, site_token=token)
                # The second run reuses the first run's still-valid owner-approved grant.
                self.assertEqual(response["request"]["access_request"]["status"], "approved" if credentialed else "pending")
                if credentialed:
                    self.assertIsNone(response["approval"])
                else:
                    self.assertEqual(response["approval"]["access_request"]["status"], "approved")
                self.assertTrue(response["verify"]["verified"])
                self.assertEqual(
                    [topic["topic"] for topic in response["projection"]["projection"]["topics"]],
                    ["orf:media/podcasts"],
                )

    def test_pilot_dry_run_signs_consent_and_checks_actual_revocation(self) -> None:
        dry_run = load_example_module("pilot_dry_run")

        def sender(method, url, body=None, **kwargs):
            response = self.client.request(method, url, json=body, headers=kwargs.get("extra_headers"))
            if response.status_code >= 400:
                raise PartnerSDKError("Test API error", response.status_code, response.json()["detail"])
            return response.json()

        for credentialed in (False, True):
            site_token = "dry-run-backend-test-" + "x" * 43 if credentialed else None
            sync_token = "dry-run-sync-test" if credentialed else None
            self.app = create_app(self.db_path, sync_token=sync_token, site_token_hashes=(
                {"open-news-demo": hashlib.sha256(site_token.encode()).hexdigest()} if credentialed else None))
            self.client = TestClient(self.app)
            output = io.StringIO()
            with self.subTest(credentialed=credentialed), \
                    patch.object(dry_run, "send_json", side_effect=sender), \
                    patch("open_recommender.partner_sdk._http_send", side_effect=sender), redirect_stdout(output):
                result = dry_run.run_pilot_dry_run("http://testserver", site_token=site_token, sync_token=sync_token)
                self.assertEqual(len(result["pull_resp"]["events"]), 4)
                grants = self.app.state.store.list_grants(profile_id=result["profile_id"])
                self.assertEqual(grants[0]["status"], "revoked")
                if credentialed:
                    self.assertNotIn(site_token, output.getvalue())
                    self.assertNotIn(sync_token, output.getvalue())
