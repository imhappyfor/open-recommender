from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.partner_sdk import PartnerClient, PartnerSDKError
from open_recommender.service import create_app


class PartnerSDKTests(unittest.TestCase):
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
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        self.profile.apply_event(event)
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.partner = PartnerClient("http://testserver", send_json=self._send_json)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _send_json(self, method: str, url: str, body: dict | None = None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
            raise PartnerSDKError(
                message=f"ORF API call failed for {method} {url}",
                status_code=response.status_code,
                detail=detail,
            )
        return response.json()

    def test_partner_sdk_flow_after_manual_approval(self) -> None:
        created = self.partner.create_access_request(
            profile_id=self.profile.profile_id,
            site_id="open-news-demo",
            purpose="Personalize the pilot site feed.",
            required_scopes=["profile.read"],
            optional_scopes=["topics.public", "topics.selective:orf:media/podcasts"],
        )
        request_id = created["access_request"]["request_id"]
        self.assertEqual(created["access_request"]["required_scopes"], ["profile.read"])
        self.assertEqual(
            created["access_request"]["optional_scopes"],
            ["topics.public", "topics.selective:orf:media/podcasts"],
        )

        approval = self.client.post(f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approval.status_code, 200)

        exchange = self.partner.exchange_access_request(request_id)
        signature = sign_payload(exchange["challenge_payload"], self.private_key)
        verify = self.partner.verify_access_request(
            request_id=request_id,
            challenge_id=exchange["challenge"]["challenge_id"],
            signature=signature,
        )

        projection = self.partner.get_projection(verify["session"]["session_id"])
        self.assertEqual(projection["projection"]["site_id"], "open-news-demo")
        self.assertEqual(
            [topic["topic"] for topic in projection["projection"]["topics"]],
            ["orf:media/podcasts"],
        )

    def test_partner_sdk_surfaces_exchange_errors(self) -> None:
        created = self.partner.create_access_request(
            profile_id=self.profile.profile_id,
            site_id="open-news-demo",
            purpose="Personalize the pilot site feed.",
            requested_scopes=["topics.public"],
        )
        request_id = created["access_request"]["request_id"]

        with self.assertRaises(PartnerSDKError) as context:
            self.partner.exchange_access_request(request_id)
        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("not approved", str(context.exception.detail))

    def test_partner_sdk_rejects_mixed_legacy_and_explicit_scope_fields(self) -> None:
        with self.assertRaises(ValueError):
            self.partner.create_access_request(
                profile_id=self.profile.profile_id,
                site_id="open-news-demo",
                purpose="Personalize the pilot site feed.",
                requested_scopes=["profile.read"],
                required_scopes=["profile.read"],
            )
