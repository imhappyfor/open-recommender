from __future__ import annotations

import io
import json
import tempfile
import unittest
from base64 import b64encode
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from open_recommender import cli
from open_recommender.crypto import generate_key_pair, private_key_public_key_b64, save_private_key, sign_payload
from open_recommender.models import EventOp, ORFProfile, build_signed_event
from open_recommender.service import create_app


class SeedCatalogTests(unittest.TestCase):
    def test_seed_catalog_covers_default_seed_volume(self) -> None:
        self.assertGreaterEqual(len(cli.SEED_TOPICS), cli.DEFAULT_SEED_TOPIC_COUNT)
        self.assertGreaterEqual(len(cli.SEED_SITES), 1)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "service.db"
        self.app = create_app(db_path)
        self.client = TestClient(self.app)
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")
        self._signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        self._signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
        )
        self.client.post("/profiles", json={"profile": self.profile.to_document()})
        self.server = "http://testserver"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _signed_event(self, op: EventOp, payload: dict) -> dict:
        event = build_signed_event(self.profile, op, payload, signature="")
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        self.profile.apply_event(event)
        return event.to_dict()

    def _send_json(self, method: str, url: str, body: dict | None = None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.client.request(method, path, json=body)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def _run_cli(self, *argv: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with patch("open_recommender.cli.send_json", side_effect=self._send_json):
            with redirect_stdout(stdout):
                exit_code = cli.main(list(argv))
        return exit_code, json.loads(stdout.getvalue())

    def _run_local_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(list(argv))
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_cli_can_inspect_approve_and_fetch_projection(self) -> None:
        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": [
                    "profile.read",
                    "topics.public",
                    "topics.selective:orf:media/podcasts",
                ],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        exit_code, inspect_body = self._run_cli(
            "site-access-request-get",
            request_id,
            self.server,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(inspect_body["profile_id"], self.profile.profile_id)
        self.assertEqual(inspect_body["access_request"]["status"], "pending")

        exit_code, approve_body = self._run_cli(
            "site-access-request-approve",
            request_id,
            self.server,
            "--scope",
            "profile.read",
            "--scope",
            "topics.public",
            "--scope",
            "topics.selective:orf:media/podcasts",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(approve_body["access_request"]["status"], "approved")
        self.assertEqual(
            approve_body["grant"]["approved_scopes"],
            [
                "profile.read",
                "topics.public",
                "topics.selective:orf:media/podcasts",
            ],
        )

        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 200)
        exchange_body = exchange_response.json()
        signature = sign_payload(exchange_body["challenge_payload"], self.private_key)

        verify_response = self.client.post(
            f"/site-access-requests/{request_id}/verify",
            json={
                "challenge_id": exchange_body["challenge"]["challenge_id"],
                "signature": signature,
            },
        )
        self.assertEqual(verify_response.status_code, 200)
        session_id = verify_response.json()["session"]["session_id"]

        exit_code, projection_body = self._run_cli(
            "grant-session-projection",
            session_id,
            self.server,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(projection_body["session"]["session_id"], session_id)
        self.assertEqual(
            [topic["topic"] for topic in projection_body["projection"]["topics"]],
            ["orf:media/podcasts", "orf:technology/python"],
        )

    def test_cli_can_deny_site_access_request(self) -> None:
        request_response = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={
                "site_id": "open-news-demo",
                "purpose": "Personalize the pilot site feed.",
                "requested_scopes": ["topics.public"],
            },
        )
        self.assertEqual(request_response.status_code, 200)
        request_id = request_response.json()["access_request"]["request_id"]

        exit_code, deny_body = self._run_cli(
            "site-access-request-deny",
            request_id,
            self.server,
            "--reason",
            "User declined this pilot request.",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(deny_body["access_request"]["status"], "denied")
        self.assertEqual(
            deny_body["access_request"]["denial_reason"],
            "User declined this pilot request.",
        )
        exchange_response = self.client.post(f"/site-access-requests/{request_id}/exchange")
        self.assertEqual(exchange_response.status_code, 400)

    def test_cli_backup_create_and_restore_round_trip(self) -> None:
        profile_path = Path(self.temp_dir.name) / "backup-source.orf"
        key_path = Path(self.temp_dir.name) / "backup-source.orf.key"
        profile_path.write_text(
            json.dumps(self.profile.to_document(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        save_private_key(key_path, self.private_key)
        backup_path = Path(self.temp_dir.name) / "alice-backup.orfb"

        create_code, create_stdout, create_stderr = self._run_local_cli(
            "backup-create",
            str(profile_path),
            str(backup_path),
            "--backup-passphrase",
            "backup-passphrase",
        )
        self.assertEqual(create_code, 0, create_stderr)
        self.assertIn("alice-backup.orfb", create_stdout)

        restored_profile_path = Path(self.temp_dir.name) / "restored.orf"
        restored_key_path = Path(self.temp_dir.name) / "restored.orf.key"
        restore_code, restore_stdout, restore_stderr = self._run_local_cli(
            "backup-restore",
            str(backup_path),
            str(restored_profile_path),
            "--key-path",
            str(restored_key_path),
            "--backup-passphrase",
            "backup-passphrase",
        )
        self.assertEqual(restore_code, 0, restore_stderr)
        self.assertIn("restored.orf", restore_stdout)

        restored_doc = json.loads(restored_profile_path.read_text(encoding="utf-8"))
        restored_profile = ORFProfile.from_document(restored_doc)
        self.assertEqual(restored_profile.profile_id, self.profile.profile_id)
        restored_private_key = cli.load_private_key(restored_key_path, passphrase="backup-passphrase")
        self.assertEqual(private_key_public_key_b64(restored_private_key), self.profile.public_key)

    def test_cli_backup_restore_rejects_mismatched_key(self) -> None:
        other_private_key, _ = generate_key_pair()
        backup_doc = {
            "backup_schema": "orf-backup.v1",
            "profile": self.profile.to_document(),
            "private_key": {
                "encoding": "pem-pkcs8",
                "encrypted": True,
                "pem_b64": b64encode(
                    other_private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.BestAvailableEncryption(
                            b"backup-passphrase"
                        ),
                    )
                ).decode("ascii"),
            },
        }
        backup_path = Path(self.temp_dir.name) / "bad-backup.orfb"
        backup_path.write_text(json.dumps(backup_doc, indent=2, sort_keys=True), encoding="utf-8")

        restore_code, _, restore_stderr = self._run_local_cli(
            "backup-restore",
            str(backup_path),
            str(Path(self.temp_dir.name) / "should-not-restore.orf"),
            "--backup-passphrase",
            "backup-passphrase",
        )
        self.assertEqual(restore_code, 1)
        self.assertIn("does not match the profile", restore_stderr)

    def test_cli_create_with_seed_populates_profile(self) -> None:
        profile_path = Path(self.temp_dir.name) / "seeded-create.orf"
        key_path = Path(self.temp_dir.name) / "seeded-create.orf.key"

        create_code, create_stdout, create_stderr = self._run_local_cli(
            "create",
            str(profile_path),
            "--display-name",
            "Seeded User",
            "--device-id",
            "seed-device",
            "--seed",
            "--seed-value",
            "1234",
            "--topic-count",
            "8",
            "--recommendation-count",
            "6",
            "--days",
            "14",
        )
        self.assertEqual(create_code, 0, create_stderr)
        output = json.loads(create_stdout)
        self.assertEqual(output["seed"]["seed_value"], 1234)
        self.assertEqual(output["seed"]["days_simulated"], 14)
        self.assertEqual(output["seed"]["topics_added"], 8)
        self.assertEqual(output["seed"]["topic_update_events_added"], 14)
        self.assertEqual(output["seed"]["recommendation_events_added"], 6)
        self.assertTrue(profile_path.exists())
        self.assertTrue(key_path.exists())

        seeded_profile = cli.load_profile(profile_path)
        self.assertEqual(len(seeded_profile.topics), 8)
        self.assertEqual(len(seeded_profile.event_log), output["seed"]["total_events_added"])
        self.assertTrue(
            any(event.op == EventOp.RECOMMEND for event in seeded_profile.event_log)
        )
        self.assertEqual(seeded_profile.created_at, output["seed"]["first_event_at"])
        first_event_at = datetime.fromisoformat(output["seed"]["first_event_at"])
        last_event_at = datetime.fromisoformat(output["seed"]["last_event_at"])
        self.assertGreaterEqual(last_event_at - first_event_at, timedelta(days=10))

    def test_cli_create_with_seed_defaults_to_month_scale_history(self) -> None:
        profile_path = Path(self.temp_dir.name) / "seeded-defaults.orf"

        create_code, create_stdout, create_stderr = self._run_local_cli(
            "create",
            str(profile_path),
            "--display-name",
            "Seeded User",
            "--device-id",
            "seed-device",
            "--seed",
            "--seed-value",
            "2024",
        )
        self.assertEqual(create_code, 0, create_stderr)
        output = json.loads(create_stdout)
        seed = output["seed"]
        self.assertEqual(seed["days_simulated"], cli.DEFAULT_SEED_ACTIVITY_DAYS)
        self.assertEqual(seed["topics_added"], cli.DEFAULT_SEED_TOPIC_COUNT)
        self.assertEqual(
            seed["recommendation_events_added"],
            cli.DEFAULT_SEED_RECOMMENDATION_COUNT,
        )
        self.assertGreaterEqual(seed["topic_update_events_added"], cli.DEFAULT_SEED_ACTIVITY_DAYS)

        seeded_profile = cli.load_profile(profile_path)
        self.assertEqual(len(seeded_profile.event_log), seed["total_events_added"])
        self.assertEqual(seeded_profile.created_at, seed["first_event_at"])
        first_event_at = datetime.fromisoformat(seed["first_event_at"])
        last_event_at = datetime.fromisoformat(seed["last_event_at"])
        self.assertGreaterEqual(last_event_at - first_event_at, timedelta(days=25))

    def test_cli_seed_command_populates_existing_profile(self) -> None:
        profile_path = Path(self.temp_dir.name) / "existing.orf"
        key_path = Path(self.temp_dir.name) / "existing.orf.key"
        empty_private_key, empty_public_key = generate_key_pair()
        empty_profile = ORFProfile.create("Existing User", empty_public_key, "seed-device")
        cli.save_profile(profile_path, empty_profile)
        save_private_key(key_path, empty_private_key)

        seed_code, seed_stdout, seed_stderr = self._run_local_cli(
            "seed",
            str(profile_path),
            "--seed-value",
            "77",
            "--topic-count",
            "10",
            "--recommendation-count",
            "4",
            "--days",
            "21",
        )
        self.assertEqual(seed_code, 0, seed_stderr)
        output = json.loads(seed_stdout)
        self.assertEqual(output["seed"]["seed_value"], 77)
        self.assertEqual(output["seed"]["days_simulated"], 21)
        self.assertEqual(output["seed"]["topics_added"], 10)
        self.assertEqual(output["seed"]["recommendation_events_added"], 4)

        seeded_profile = cli.load_profile(profile_path)
        self.assertEqual(len(seeded_profile.topics), 10)
        self.assertGreaterEqual(len(seeded_profile.opt_out_topics), 1)

        feed_code, feed_stdout, feed_stderr = self._run_local_cli(
            "feed",
            "show",
            str(profile_path),
            "--top-n",
            "5",
        )
        self.assertEqual(feed_code, 0, feed_stderr)
        feed_output = json.loads(feed_stdout)
        self.assertGreater(feed_output["feed_size"], 0)

    def test_cli_feed_show_aggregates_recommendations(self) -> None:
        """Feed show command displays aggregated recommendations from profile."""
        profile_path = Path(self.temp_dir.name) / "alice.orf"
        key_path = Path(self.temp_dir.name) / "alice.orf.key"
        
        # Save the profile with recommendations
        self._signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-123",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.9,
                "metadata": {"title": "The Matrix"},
            },
        )
        self._signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-123",
                "site_id": "imdb",
                "site_name": "IMDb",
                "score": 0.85,
                "metadata": {"title": "The Matrix"},
            },
        )
        self._signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "podcast-456",
                "site_id": "spotify",
                "site_name": "Spotify",
                "score": 0.8,
                "metadata": {"title": "Tech Podcast"},
            },
        )
        
        cli.save_profile(profile_path, self.profile)
        save_private_key(key_path, self.private_key)
        
        # Run feed show command
        code, stdout, stderr = self._run_local_cli(
            "feed", "show", str(profile_path), "--top-n", "10"
        )
        
        self.assertEqual(code, 0, f"CLI failed: {stderr}")
        
        output = json.loads(stdout)
        self.assertEqual(output["feed_size"], 2)  # movie-123 (de-dup) + podcast-456
        self.assertEqual(len(output["recommendations"]), 2)
        
        # Check that movie-123 has 2 sources (de-duplicated)
        movie = next(r for r in output["recommendations"] if r["item_id"] == "movie-123")
        self.assertEqual(len(movie["sources"]), 2)
        
        # Check that consensus score is correct for movie-123
        # 2 unique sites out of 3 total = 2/3 ≈ 0.667
        self.assertGreater(movie["consensus_score"], 0.6)
        self.assertLess(movie["consensus_score"], 0.75)
