import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import ORFProfile, build_registration_event
from open_recommender.partner_sdk import PartnerClient, PartnerSDKError, _NoCredentialRedirect
from open_recommender.service import create_app
from owner_helpers import owner_post


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class PartnerAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "auth.db"
        self.token = "a" * 43
        self.hashes = {"open-news-demo": token_hash(self.token), "other-site": token_hash("b" * 43)}
        self.sites = [{"site_id": site_id, "site_name": site_id, "allowed_scopes": ["profile.read"]}
                      for site_id in self.hashes]
        self.app = create_app(self.db, pilot_sites=self.sites, site_token_hashes=self.hashes,
                              rate_limit_max_requests=1000)
        self.client = TestClient(self.app)
        self.headers = {"X-ORF-Site-ID": "open-news-demo", "X-ORF-Site-Token": self.token}
        self.other_headers = {"X-ORF-Site-ID": "other-site", "X-ORF-Site-Token": "b" * 43}
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        self.profile.apply_event(registration)
        response = self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.assertEqual(response.status_code, 200, response.text)

    def send(self, method, url, body=None, *, extra_headers=None):
        response = self.client.request(method, url, json=body, headers=extra_headers)
        if response.status_code >= 400:
            raise PartnerSDKError("Test request failed", response.status_code, response.json()["detail"])
        return response.json()

    def test_backend_flow_and_all_partner_boundaries(self):
        sdk = PartnerClient("http://testserver", site_id="open-news-demo", site_token=self.token,
                            send_json=self.send)
        created = sdk.create_access_request(profile_id=self.profile.profile_id, site_id="open-news-demo",
                                            purpose="Test", required_scopes=["profile.read"])
        request_id = created["access_request"]["request_id"]
        self.assertEqual(sdk.get_access_request(request_id)["access_request"]["status"], "pending")
        approved = owner_post(self.client, self.profile, self.key, f"/site-access-requests/{request_id}/approve")
        self.assertEqual(approved.status_code, 200, approved.text)
        exchange = sdk.exchange_access_request(request_id)
        proof = {"challenge_id": exchange["challenge"]["challenge_id"],
                 "signature": sign_payload(exchange["challenge_payload"], self.key)}
        # A wrong site cannot consume the user's valid exchange proof.
        verify_path = f"/site-access-requests/{request_id}/verify"
        self.assertEqual(self.client.post(verify_path, json=proof, headers=self.other_headers).status_code, 404)
        verified = sdk.verify_access_request(request_id=request_id, **proof)
        session_id = verified["session"]["session_id"]
        self.assertEqual(sdk.get_projection(session_id)["projection"]["site_id"], "open-news-demo")
        candidates = [{"candidate_id": "one", "site_score": 0.7, "candidate_topics": []}]
        self.assertEqual(len(sdk.rank_candidates(session_id, candidates=candidates)["ranking"]["ranked_candidates"]), 1)
        events = [{"event_id": "click-one", "event_type": "click", "candidate_id": "one"}]
        sdk.record_ranking_feedback(session_id, events=events)

        routes = [
            ("POST", f"/profiles/{self.profile.profile_id}/site-access-requests",
             {"site_id": "open-news-demo", "purpose": "Test", "required_scopes": ["profile.read"]}),
            ("GET", f"/site-access-requests/{request_id}", None),
            ("POST", f"/site-access-requests/{request_id}/exchange", None),
            ("POST", verify_path, proof),
            ("GET", f"/grant-sessions/{session_id}/projection", None),
            ("POST", f"/grant-sessions/{session_id}/rank", {"candidates": candidates}),
            ("POST", f"/grant-sessions/{session_id}/rank/feedback", {"events": events}),
        ]
        with sqlite3.connect(self.db) as connection:
            before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("audit_events", "challenges", "grant_sessions", "ranking_feedback_events", "access_requests")}
        for method, path, body in routes:
            for headers, status in (
                ({}, 401), ({**self.headers, "X-ORF-Site-Token": "wrong"}, 401),
                ({**self.headers, "X-ORF-Site-ID": "unconfigured"}, 401),
                (self.other_headers, 403 if path.endswith("/site-access-requests") else 404),
            ):
                with self.subTest(path=path, headers=headers["X-ORF-Site-ID"] if headers else "missing"):
                    response = self.client.request(method, path, json=body, headers=headers)
                    self.assertEqual(response.status_code, status, response.text)
        with sqlite3.connect(self.db) as connection:
            after = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
        self.assertEqual(before, after)
        duplicate = list(self.headers.items()) + [("X-ORF-Site-Token", self.token)]
        self.assertEqual(self.client.get(routes[1][1], headers=duplicate).status_code, 401)
        # A backend key never substitutes for an owner proof, even on an unprotected owner route.
        for path in (f"/site-access-requests/{request_id}/approve", f"/site-access-requests/{request_id}/deny",
                     f"/grants/{verified['grant']['grant_id']}/revoke", f"/profiles/{self.profile.profile_id}/delete",
                     f"/profiles/{self.profile.profile_id}/events/read"):
            self.assertEqual(self.client.post(path, json={}, headers=self.headers).status_code, 401)
        revoke = owner_post(self.client, self.profile, self.key, f"/grants/{verified['grant']['grant_id']}/revoke")
        self.assertEqual(revoke.status_code, 200)
        with self.assertRaises(PartnerSDKError) as error:
            sdk.get_projection(session_id)
        self.assertEqual(error.exception.status_code, 400)

    def test_configuration_is_fail_closed_and_rotation_takes_effect(self):
        invalid_configs = [[], {"": "0" * 64}, {"open-news-demo": "short"},
                           {"open-news-demo": "A" * 64}, {"open-news-demo": None},
                           {"unknown-site": "0" * 64}, {site: "0" * 64 for site in self.hashes}]
        for config in invalid_configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                create_app(self.db, pilot_sites=self.sites, site_token_hashes=config)
        for raw in ("", "null", "[]", '{"open-news-demo":"short"}'):
            with patch.dict("os.environ", {"OPEN_RECOMMENDER_SITE_TOKEN_HASHES": raw}), self.assertRaises(ValueError):
                create_app(self.db)
        with patch.dict("os.environ", {"OPEN_RECOMMENDER_SITE_TOKEN_HASHES": json.dumps(self.hashes)}):
            app = create_app(self.db, pilot_sites=self.sites)
            self.assertTrue(TestClient(app).get("/health").json()["service"]["site_auth_required"])
            disabled = TestClient(create_app(self.db, site_token_hashes={}))
            self.assertEqual(disabled.get("/site-access-requests/unknown", headers=self.headers).status_code, 401)
        health = self.client.get("/health").text
        self.assertNotIn(self.token, health)
        self.assertNotIn(self.hashes["open-news-demo"], health)
        new_token = "c" * 43
        rotated = TestClient(create_app(self.db, pilot_sites=self.sites,
            site_token_hashes={"open-news-demo": token_hash(new_token)}))
        self.assertEqual(rotated.get("/site-access-requests/unknown", headers=self.headers).status_code, 401)
        self.assertEqual(rotated.get("/site-access-requests/unknown", headers={
            **self.headers, "X-ORF-Site-Token": new_token}).status_code, 404)
        for options in ({"site_id": "open-news-demo"}, {"site_token": self.token},
                        {"site_id": "", "site_token": self.token}):
            with self.assertRaises(ValueError):
                PartnerClient("http://testserver", **options)

    def test_sdk_keeps_credentials_in_their_lane_and_refuses_redirects(self):
        sent = []

        def sender(method, url, body=None, *, extra_headers=None):
            sent.append(extra_headers)
            return {}

        sdk = PartnerClient("https://orf.example", site_id="open-news-demo", site_token=self.token,
                            sync_token="sync-secret", send_json=sender)
        sdk.get_access_request("request-one")
        sdk.push_events(self.profile.profile_id, [])
        self.assertEqual(sent, [self.headers, {"Authorization": "Bearer sync-secret"}])
        handler = _NoCredentialRedirect()
        req = request.Request("https://orf.example", headers=self.headers)
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(req, None, code, "Redirect", {}, "https://other.example"))
        with patch("open_recommender.partner_sdk.request.build_opener") as opener, \
             patch("open_recommender.partner_sdk.request.urlopen") as direct:
            opener.return_value.open.side_effect = error.HTTPError(
                "https://orf.example", 302, "Redirect", {}, io.BytesIO(b"{}"))
            with self.assertRaises(PartnerSDKError) as failure:
                PartnerClient("https://orf.example", site_id="open-news-demo",
                              site_token=self.token).get_projection("session-one")
            self.assertEqual(failure.exception.status_code, 302)
            self.assertEqual(opener.return_value.open.call_args.kwargs, {"timeout": 30})
            self.assertIsInstance(opener.call_args.args[0], _NoCredentialRedirect)
            direct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
