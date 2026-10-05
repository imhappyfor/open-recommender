import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from open_recommender.crypto import generate_key_pair, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
from open_recommender.service import FixedWindowRateLimiter, RequestBodyLimitMiddleware, create_app
from owner_helpers import owner_post


class BodyLimitTests(unittest.TestCase):
    def run_body(self, messages, headers=((b"content-type", b"application/octet-stream"),), scope_type="http"):
        async def run():
            pending = iter(messages)
            result = {"called": False, "received": 0, "sent": []}

            async def receive():
                result["received"] += 1
                return next(pending, {"type": "http.disconnect"})

            async def send(message):
                result["sent"].append(message)

            async def child(scope, receive, send):
                result["called"] = True
                if scope["type"] == "http":
                    result["body"] = await receive()
                    await JSONResponse({"ok": True})(scope, receive, send)

            middleware = RequestBodyLimitMiddleware(child, max_bytes=4)
            await middleware({"type": scope_type, "headers": list(headers)}, receive, send)
            return result
        return asyncio.run(run())

    def test_declared_size_rejects_early_but_never_replaces_actual_byte_count(self):
        for value, status in ((b"5", 413), (b"invalid", 400), (b"-1", 400)):
            result = self.run_body([], [(b"Content-Length", value)])
            self.assertEqual(result["sent"][0]["status"], status)
            self.assertEqual(result["received"], 0)
            self.assertFalse(result["called"])
        for headers in ([], [(b"content-length", b"1")]):
            result = self.run_body([
                {"type": "http.request", "body": b"ab", "more_body": True},
                {"type": "http.request", "body": b"cde", "more_body": True},
            ], headers)
            self.assertEqual(result["sent"][0]["status"], 413)
            self.assertEqual(result["received"], 2)
            self.assertFalse(result["called"])

    def test_exact_limit_replays_body_once_and_disconnects_do_not_call_app(self):
        result = self.run_body([
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"cd"},
        ])
        self.assertEqual(result["sent"][0]["status"], 200)
        self.assertEqual(result["body"], {"type": "http.request", "body": b"abcd", "more_body": False})
        result = self.run_body([
            {"type": "http.request", "body": b"ab", "more_body": True}, {"type": "http.disconnect"},
        ])
        self.assertFalse(result["called"])
        self.assertEqual(result["sent"], [])
        result = self.run_body([], scope_type="lifespan")
        self.assertTrue(result["called"])
        self.assertEqual(result["received"], 0)


class RateLimiterTests(unittest.TestCase):
    def test_buckets_are_bounded_expire_and_allow_existing_clients_at_capacity(self):
        limiter = FixedWindowRateLimiter(window_seconds=10, max_requests=3, max_buckets=2)
        with patch("open_recommender.service.time.monotonic", return_value=0):
            limiter.check("write", "one")
            limiter.check("write", "two")
            with self.assertRaises(HTTPException) as error:
                limiter.check("write", "three")
            self.assertEqual(error.exception.status_code, 429)
            self.assertEqual(error.exception.headers["Retry-After"], "10")
            limiter.check("write", "one")
            self.assertEqual(len(limiter._buckets), 2)
        with patch("open_recommender.service.time.monotonic", return_value=11):
            limiter.check("write", "three")
            self.assertEqual(len(limiter._buckets), 1)

    def test_concurrent_requests_cannot_lose_counter_updates(self):
        limiter = FixedWindowRateLimiter(window_seconds=10, max_requests=3)

        def attempt(_):
            try:
                limiter.check("write", "same-client")
                return 200
            except HTTPException as error:
                return error.status_code

        with patch("open_recommender.service.time.monotonic", return_value=0), ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(attempt, range(40)))
        self.assertEqual(codes.count(200), 3)
        self.assertEqual(codes.count(429), 37)


class ResourceLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "limits.db"
        self.configure()
        self.key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.key)
        self.profile.apply_event(registration)
        response = self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.assertEqual(response.status_code, 200, response.text)

    def configure(self, **limits):
        self.app = create_app(self.db_path, rate_limit_max_requests=500, **limits)
        self.client = TestClient(self.app)

    def event(self, topic="orf:health/sleep", padding=0):
        event = build_signed_event(self.profile, EventOp.SET_TOPIC,
            {"topic": topic, "weight": 0.7, "visibility": "private", "padding": "x" * padding}, signature="")
        event.signature = sign_payload(event.unsigned_payload(), self.key)
        return event.to_dict()

    def push(self, events):
        return self.client.post(f"/profiles/{self.profile.profile_id}/events", json={"events": events})

    def session(self):
        request_id = self.client.post(f"/profiles/{self.profile.profile_id}/site-access-requests", json={
            "site_id": "open-news-demo", "purpose": "Resource test", "required_scopes": ["profile.read"],
        }).json()["access_request"]["request_id"]
        response = owner_post(self.client, self.profile, self.key,
            f"/site-access-requests/{request_id}/approve", json={"approved_scopes": ["profile.read"]})
        self.assertEqual(response.status_code, 200, response.text)
        exchange = self.client.post(f"/site-access-requests/{request_id}/exchange").json()
        return self.client.post(f"/site-access-requests/{request_id}/verify", json={
            "challenge_id": exchange["challenge"]["challenge_id"],
            "signature": sign_payload(exchange["challenge_payload"], self.key),
        }).json()["session"]["session_id"]

    def test_http_body_limits_preserve_cache_and_cors_headers(self):
        self.configure(max_request_bytes=64)
        headers = {"Content-Type": "application/json", "Origin": "http://localhost:5173"}
        for content, status in ((b"{}" + b" " * 62, 400), (b"{}" + b" " * 63, 413)):
            response = self.client.post("/profiles", content=content, headers=headers)
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], headers["Origin"])
        response = self.client.post("/profiles", content=b"x" * 65,
            headers={**headers, "Content-Length": "1"})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/profiles", content=iter([b"x" * 32, b"x" * 33]), headers=headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.app.state.store.list_events(self.profile.profile_id), [])

    def test_profile_count_limit_is_cumulative_atomic_and_idempotent(self):
        self.configure(max_profile_events=3)
        first = self.event()
        self.assertEqual(self.push([first]).status_code, 200)
        next_events = [self.event(topic) for topic in ("orf:health/exercise", "orf:technology/python")]
        self.assertEqual(self.push(next_events).status_code, 413)
        self.assertEqual(len(self.app.state.store.list_events(self.profile.profile_id)), 1)
        self.assertEqual(self.push([next_events[0]]).status_code, 200)
        self.assertEqual(self.push([first, next_events[0]]).status_code, 200)
        self.assertEqual(self.push([next_events[1]]).status_code, 413)
        for path in ("/profiles", "/lens/profiles/import"):
            doc = self.profile.to_document()
            doc["event_log"] += [first, *next_events]
            response = self.client.post(path, json={"profile": doc})
            self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.push([{}] * 4).status_code, 413)
        self.assertEqual(len(self.app.state.store.list_events(self.profile.profile_id)), 2)

    def test_profile_byte_limit_rolls_back_and_allows_retries_at_the_exact_budget(self):
        event = self.event()
        registration_size = len(json.dumps(self.profile.event_log[0].to_dict(), sort_keys=True))
        self.configure(max_history_bytes=registration_size + len(json.dumps(event, sort_keys=True)))
        large = self.event("orf:health/exercise", padding=1000)
        self.assertEqual(self.push([event, large]).status_code, 413)
        self.assertEqual(self.app.state.store.list_events(self.profile.profile_id), [])
        self.assertEqual(self.push([event]).status_code, 200)
        self.assertEqual(self.push([event]).status_code, 200)
        self.assertEqual(self.push([large]).status_code, 413)

    def test_ranking_and_feedback_batch_limits_are_checked_before_item_parsing(self):
        self.configure(max_ranking_candidates=1, max_feedback_batch=1)
        session_id = self.session()
        for suffix, body in (("rank", {"candidates": [None, None]}), ("rank/feedback", {"events": [None, None]})):
            response = self.client.post(f"/grant-sessions/{session_id}/{suffix}", json=body)
            self.assertEqual(response.status_code, 413, response.text)
        response = self.client.post(f"/grant-sessions/{session_id}/rank", json={
            "candidates": [{"candidate_id": "story", "site_score": 0.7}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/grant-sessions/{session_id}/rank/feedback", json={
            "events": [{"event_id": "feedback", "candidate_id": "story", "event_type": "click"}],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def test_feedback_history_budget_is_cumulative_and_batches_roll_back(self):
        self.configure(max_grant_feedback_events=2)
        session_id = self.session()
        events = [{"event_id": f"feedback-{n}", "candidate_id": "story", "event_type": "click"} for n in range(3)]
        path = f"/grant-sessions/{session_id}/rank/feedback"
        self.assertEqual(self.client.post(path, json={"events": [events[0]]}).status_code, 200)
        self.assertEqual(self.client.post(path, json={"events": events[1:]}).status_code, 413)
        with self.app.state.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ranking_feedback_events").fetchone()[0], 1)
        self.assertEqual(self.client.post(path, json={"events": events[:2]}).status_code, 200)
        response = self.client.post(path, json={"events": events[:2]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["feedback"]["accepted_events"], 0)
        self.assertEqual(self.client.post(path, json={"events": [events[2]]}).status_code, 413)
        large = {**events[2], "metadata": {"padding": "x" * 4000}}
        self.configure(max_history_bytes=2000)
        self.assertEqual(self.client.post(path, json={"events": [large]}).status_code, 413)
        with self.app.state.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ranking_feedback_events").fetchone()[0], 2)

    def test_concurrent_writes_cannot_exceed_the_last_history_slot(self):
        self.configure(max_profile_events=2, max_grant_feedback_events=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda event: self.push([event]),
                [self.event("orf:health/exercise"), self.event("orf:technology/python")]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 413])
        self.assertEqual(len(self.app.state.store.list_events(self.profile.profile_id)), 1)
        session_id = self.session()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda n: self.client.post(f"/grant-sessions/{session_id}/rank/feedback",
                json={"events": [{"event_id": f"last-{n}", "candidate_id": "story", "event_type": "click"}]}), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 413])
        with self.app.state.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ranking_feedback_events").fetchone()[0], 1)

    def test_limits_are_reported_configurable_and_fail_fast_on_invalid_values(self):
        self.configure(max_request_bytes=4096, max_ranking_candidates=7)
        limits = self.client.get("/health").json()["service"]["limits"]
        self.assertEqual(limits["max_request_bytes"], 4096)
        self.assertEqual(limits["max_ranking_candidates"], 7)
        for name in limits:
            for value in (0, -1, True):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    create_app(self.db_path, **{name: value})
        with patch.dict("os.environ", {"OPEN_RECOMMENDER_MAX_REQUEST_BYTES": "8192"}):
            self.assertEqual(create_app(self.db_path).state.config.max_request_bytes, 8192)

    def test_profile_write_surfaces_share_a_rate_bucket_and_send_retry_after(self):
        self.app = create_app(self.db_path, rate_limit_max_requests=2)
        self.client = TestClient(self.app)
        doc = {"profile": self.profile.to_document()}
        self.assertEqual(self.client.post("/profiles", json=doc).status_code, 200)
        self.assertEqual(self.push([]).status_code, 200)
        response = self.client.post("/lens/profiles/import", json=doc)
        self.assertEqual(response.status_code, 429, response.text)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
