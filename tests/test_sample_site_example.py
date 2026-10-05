from __future__ import annotations

import importlib.util
import hashlib
from html import escape
from html.parser import HTMLParser
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, save_private_key, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import create_app
from open_recommender.partner_sdk import PartnerSDKError
from owner_helpers import owner_post


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
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.private_key)
        self.profile.apply_event(registration)
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

    def _send_json(self, method: str, url: str, body: dict | None = None, *, extra_headers=None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.orf_client.request(method, path, json=body, headers=extra_headers)
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

        approve_response = owner_post(self.orf_client, self.profile, self.private_key,
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
        self.assertIn("Shared preference preview", complete_response.text)
        self.assertIn("orf:technology/python", complete_response.text)
        self.assertIn("orf:media/podcasts", complete_response.text)

    def test_authenticated_sample_holds_credentials_only_in_backend(self):
        token = "sample-backend-test-token-" + "x" * 43
        self.orf_client = TestClient(create_app(self.db_path,
            site_token_hashes={"open-news-demo": hashlib.sha256(token.encode()).hexdigest()}))
        calls = []

        def sender(method, url, body=None, *, extra_headers=None):
            calls.append(extra_headers)
            return self._send_json(method, url, body, extra_headers=extra_headers)

        app = self.sample_site_module.create_sample_site_app(orf_service_url="http://testserver",
            demo_signer_key_path=self.key_path, site_token=token, send_json_fn=sender)
        client = TestClient(app)
        connected = client.get("/connect", params={"profile_id": self.profile.profile_id})
        self.assertEqual(connected.status_code, 200)
        request_id = next(iter(app.state.sessions))
        response = owner_post(self.orf_client, self.profile, self.private_key,
                              f"/site-access-requests/{request_id}/approve")
        self.assertEqual(response.status_code, 200)
        complete = client.post(f"/session/{request_id}/complete")
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertIn("Shared preference preview", complete.text)
        for response in (connected, complete):
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(token, response.text)
            self.assertNotIn("X-ORF-Site-Token", response.text)
            self.assertNotIn("PRIVATE KEY", response.text)
        self.assertTrue(calls)
        self.assertTrue(all(headers == {"X-ORF-Site-ID": "open-news-demo", "X-ORF-Site-Token": token}
                            for headers in calls))
        remote = TestClient(app, client=("198.51.100.10", 12345))
        before = len(calls)
        for method, path in (("GET", "/"), ("GET", "/connect?profile_id=test"),
                             ("GET", f"/session/{request_id}"), ("POST", f"/session/{request_id}/complete")):
            response = remote.request(method, path)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(calls), before)

    def test_sample_escapes_untrusted_html_and_hides_upstream_errors(self):
        hostile = '\"><img src=x onerror=alert(1)><script>alert(1)</script>'
        request_data = {"access_request": {"request_id": hostile, "status": hostile, "purpose": hostile,
                         "required_scopes": [hostile], "optional_scopes": [hostile]},
                        "consent_review_url": "http://testserver/" + hostile}
        html = self.sample_site_module._render_page(profile_id=hostile, request_data=request_data,
            projection={"projection": {"display_name": hostile, "topics": [{"topic": hostile, "visibility": hostile}]}},
            error_message=hostile, demo_signer_enabled=False)
        self.assertIn(escape(hostile), html)

        class Tags(HTMLParser):
            def handle_starttag(self, tag, attributes):
                self.tags.append(tag)
                self.attributes.extend(attributes)

        parser = Tags()
        parser.tags, parser.attributes = [], []
        parser.feed(html)
        self.assertNotIn("img", parser.tags)
        self.assertNotIn("script", parser.tags)
        self.assertFalse(any(name.startswith("on") for name, value in parser.attributes))
        self.assertIn(("value", hostile), parser.attributes)

        def reject(method, url, body=None):
            raise PartnerSDKError("secret-token-in-upstream-message", 401, hostile)

        client = TestClient(self.sample_site_module.create_sample_site_app(
            orf_service_url="http://testserver", send_json_fn=reject))
        response = client.get("/connect", params={"profile_id": self.profile.profile_id})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Check the backend configuration", response.text)
        self.assertNotIn("secret-token", response.text)
        self.assertNotIn(hostile, response.text)

    def test_sample_actions_follow_actual_request_state(self):
        request_data = {"access_request": {"request_id": "request-test", "purpose": "Personalize",
                        "required_scopes": ["profile.read"], "optional_scopes": [], "status": "pending"},
                        "consent_review_url": "http://testserver/consent/request-test"}
        for status in ("pending", "approved", "denied", "expired"):
            request_data["access_request"]["status"] = status
            html = self.sample_site_module._render_page(request_data=request_data, demo_signer_enabled=True)
            with self.subTest(status=status):
                self.assertIn('Technical request details', html)
                if status == "pending":
                    self.assertIn("Check approval status", html)
                    self.assertIn("rel='noopener noreferrer'", html)
                    self.assertIn("target='_blank'", html)
                    self.assertNotIn("action='/session/request-test/complete'", html)
                elif status == "approved":
                    self.assertIn("action='/session/request-test/complete'", html)
                    self.assertNotIn("Open consent review", html)
                else:
                    self.assertIn("Start again", html)
                    self.assertNotIn("Open consent review", html)
                    self.assertNotIn("action='/session/request-test/complete'", html)
        request_data["access_request"]["status"] = "approved"
        html = self.sample_site_module._render_page(request_data=request_data, demo_signer_enabled=True,
            projection={"projection": {"topics": []}})
        self.assertIn("not a live permission check", html)
        self.assertIn("not a ranked content feed", html)
        self.assertNotIn("action='/session/request-test/complete'", html)
