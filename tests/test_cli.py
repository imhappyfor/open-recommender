from __future__ import annotations

import io
import json
import os
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
from open_recommender.models import EventOp, ORFProfile, build_registration_event, build_signed_event
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
        registration = build_registration_event(self.profile)
        registration.signature = sign_payload(registration.unsigned_payload(), self.private_key)
        self.profile.apply_event(registration)
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
        self.profile_path = Path(self.temp_dir.name) / "owner.orf"
        cli.save_profile(self.profile_path, self.profile)
        save_private_key(self.profile_path.with_suffix(".orf.key"), self.private_key)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _signed_event(self, op: EventOp, payload: dict) -> dict:
        event = build_signed_event(self.profile, op, payload, signature="")
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        self.profile.apply_event(event)
        return event.to_dict()

    def _send_json(self, method: str, url: str, body: dict | None = None, *, extra_headers=None) -> dict:
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        response = self.client.request(method, path, json=body, headers=extra_headers)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def _run_cli(self, *argv: str) -> tuple[int, dict]:
        stdout = io.StringIO()
        with patch("open_recommender.cli.send_json", side_effect=self._send_json):
            with redirect_stdout(stdout):
                exit_code = cli.main(list(argv))
        return exit_code, json.loads(stdout.getvalue() or "{}")

    def _run_local_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(list(argv))
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_public_export_omits_opt_out_names_without_modifying_owner_file(self) -> None:
        topic = "orf:health/private-condition"
        self._signed_event(EventOp.SET_OPT_OUT, {"topic": topic, "value": True})
        cli.save_profile(self.profile_path, self.profile)
        original = self.profile_path.read_bytes()
        code, stdout, _ = self._run_local_cli("export-public", str(self.profile_path))
        self.assertEqual(code, 0)
        self.assertNotIn("opt_out_topics", json.loads(stdout))
        self.assertNotIn(topic, stdout)
        self.assertEqual(self.profile_path.read_bytes(), original)
        self.assertIn(topic, cli.load_profile(self.profile_path).opt_out_topics)

    def test_create_refuses_existing_identity_and_colliding_destinations(self) -> None:
        key_path = self.profile_path.with_suffix(".orf.key")
        before = [path.read_bytes() for path in (self.profile_path, key_path)]
        code, _, stderr = self._run_local_cli("create", str(self.profile_path), "--display-name", "Replacement")
        self.assertEqual(code, 1)
        self.assertIn("Refusing to overwrite", stderr)
        self.assertEqual([path.read_bytes() for path in (self.profile_path, key_path)], before)
        fresh = Path(self.temp_dir.name) / "fresh.orf"
        code, _, stderr = self._run_local_cli("create", str(fresh), "--key-path", str(fresh), "--display-name", "Fresh")
        self.assertEqual(code, 1)
        self.assertIn("different files", stderr)
        self.assertFalse(fresh.exists())

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
            "--profile-path", str(self.profile_path),
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
            "--profile-path", str(self.profile_path),
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

    def test_cli_sync_pull_signs_owner_proof_and_supports_encrypted_keys_and_token(self) -> None:
        remote = self._signed_event(EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.6, "visibility": "private"})
        self.app.state.store.append_events(self.profile.profile_id, [cli.build_event_from_remote(remote)])
        self.client = TestClient(create_app(self.app.state.config.db_path, sync_token="test-token"))
        save_private_key(self.profile_path.with_suffix(".orf.key"), self.private_key, "key-passphrase")
        with patch("open_recommender.cli.send_json", side_effect=self._send_json), \
                patch.dict("os.environ", {"OPEN_RECOMMENDER_SYNC_TOKEN": "test-token"}):
            code, _, stderr = self._run_local_cli("sync-pull", str(self.profile_path), self.server,
                "--key-passphrase", "key-passphrase")
        self.assertEqual(code, 0, stderr)
        saved = cli.load_profile(self.profile_path)
        self.assertEqual(saved.topics["orf:health/sleep"].visibility.value, "private")
        self.assertEqual(saved.event_log[-1].to_dict(), remote)

    def test_cli_sync_pull_rejects_forged_batch_without_changing_local_file(self) -> None:
        remotes = [self._signed_event(EventOp.SET_TOPIC,
            {"topic": topic, "weight": 0.6, "visibility": "private"})
            for topic in ("orf:health/sleep", "orf:health/exercise")]
        self.app.state.store.append_events(self.profile.profile_id,
            [cli.build_event_from_remote(event) for event in remotes])
        original = self.profile_path.read_bytes()

        def forged_sender(method, url, body=None, **kwargs):
            response = self._send_json(method, url, body, **kwargs)
            if url.endswith("/events/read"):
                response["events"][-1]["payload"]["weight"] = 0.1
            return response

        with patch("open_recommender.cli.send_json", side_effect=forged_sender):
            code, _, stderr = self._run_local_cli("sync-pull", str(self.profile_path), self.server)
        self.assertEqual(code, 1)
        self.assertIn("Signature verification failed", stderr)
        self.assertEqual(self.profile_path.read_bytes(), original)

    def test_cli_sync_pull_keeps_late_events_and_replays_ties_without_losing_local_changes(self) -> None:
        local = cli.load_profile(self.profile_path)
        local.sync.device_id = "offline-laptop"

        def signed(op, payload, clock, timestamp):
            event = build_signed_event(local, op, payload, signature="", clock=clock, timestamp=timestamp)
            event.signature = sign_payload(event.unsigned_payload(), self.private_key)
            return event

        local_events = [
            signed(EventOp.SET_TOPIC, {"topic": "orf:technology/python", "weight": 0.9,
                "visibility": "public"}, 10, "2026-01-02T00:00:00+00:00"),
            signed(EventOp.SET_CONSENT, {"field": "ad_personalization", "value": True},
                10, "2026-01-02T00:00:00+00:00"),
            signed(EventOp.SET_TOPIC, {"topic": "orf:health/exercise", "weight": 0.6,
                "visibility": "private"}, 20, "2026-01-03T00:00:00+00:00"),
        ]
        for event in local_events:
            local.apply_event(event)
        cli.save_profile(self.profile_path, local)
        remote_events = [
            signed(EventOp.SET_TOPIC, {"topic": "orf:health/sleep", "weight": 0.5,
                "visibility": "private"}, 1, "2026-01-01T00:00:00+00:00"),
            signed(EventOp.SET_TOPIC, {"topic": "orf:technology/python", "weight": 0.2,
                "visibility": "public"}, 10, "2026-01-01T00:00:00+00:00"),
            signed(EventOp.SET_CONSENT, {"field": "ad_personalization", "value": False},
                10, "2026-01-01T00:00:00+00:00"),
        ]
        self.app.state.store.append_events(local.profile_id, remote_events)
        self.assertEqual(self._run_cli("sync-pull", str(self.profile_path), self.server)[0], 0)
        saved = cli.load_profile(self.profile_path)
        self.assertEqual(saved.topics["orf:technology/python"].weight, 0.9)
        self.assertIn("orf:health/sleep", saved.topics)
        self.assertIn("orf:health/exercise", saved.topics)
        self.assertFalse(saved.consent.ad_personalization)
        self.assertEqual(saved.sync.device_id, "offline-laptop")
        self.assertEqual(saved.sync.last_clock, 20)
        self.assertEqual({event.event_id for event in saved.event_log},
            {event.event_id for event in [*local.event_log, *remote_events]})
        first_pull = self.profile_path.read_bytes()
        self.assertEqual(self._run_cli("sync-pull", str(self.profile_path), self.server)[0], 0)
        self.assertEqual(self.profile_path.read_bytes(), first_pull)

        late = signed(EventOp.SET_OPT_OUT, {"topic": "orf:politics/news", "value": True},
            1, "2026-01-01T00:00:00+00:00")
        self.app.state.store.append_events(local.profile_id, [late])
        self.assertEqual(self._run_cli("sync-pull", str(self.profile_path), self.server)[0], 0)
        self.assertIn("orf:politics/news", cli.load_profile(self.profile_path).opt_out_topics)

    def test_cli_sync_pull_rejects_tampered_known_events_and_signed_id_conflicts(self) -> None:
        original = self.profile_path.read_bytes()
        for resign in (False, True):
            with self.subTest(resign=resign):
                def conflicting_sender(method, url, body=None, **kwargs):
                    response = self._send_json(method, url, body, **kwargs)
                    if url.endswith("/events/read"):
                        event = response["events"][0]
                        event["payload"]["weight"] = 0.1
                        if resign:
                            altered = cli.build_event_from_remote(event)
                            event["signature"] = sign_payload(altered.unsigned_payload(), self.private_key)
                    return response

                with patch("open_recommender.cli.send_json", side_effect=conflicting_sender):
                    code, _, stderr = self._run_local_cli("sync-pull", str(self.profile_path), self.server)
                self.assertEqual(code, 1)
                self.assertIn("different event" if resign else "Signature verification failed", stderr)
                self.assertEqual(self.profile_path.read_bytes(), original)

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
        original_backup = backup_path.read_bytes()
        repeat_code, _, _ = self._run_local_cli("backup-create", str(profile_path), str(backup_path),
            "--backup-passphrase", "another-passphrase")
        self.assertEqual(repeat_code, 1)
        self.assertEqual(backup_path.read_bytes(), original_backup)
        collision_code, _, collision_error = self._run_local_cli("backup-restore", str(backup_path), str(backup_path),
            "--backup-passphrase", "backup-passphrase", "--overwrite")
        self.assertEqual(collision_code, 1)
        self.assertIn("different files", collision_error)
        self.assertEqual(backup_path.read_bytes(), original_backup)

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
        if os.name == "posix":
            for path in (key_path, backup_path, restored_profile_path, restored_key_path):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600, str(path))

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

    def test_cli_create_and_sync_push_register_signed_identity(self) -> None:
        profile_path = Path(self.temp_dir.name) / "new.orf"
        code, _, stderr = self._run_local_cli("create", str(profile_path), "--display-name", "New User")
        self.assertEqual(code, 0, stderr)
        profile = cli.load_profile(profile_path)
        self.assertEqual(profile.event_log[0].clock, 0)
        with patch("open_recommender.cli.send_json", side_effect=self._send_json):
            code, _, stderr = self._run_local_cli("sync-push", str(profile_path), self.server)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.app.state.store.get_profile(profile.profile_id).display_name, "New User")

    def test_cli_sync_push_upgrades_legacy_profile_with_encrypted_key(self) -> None:
        profile_path = Path(self.temp_dir.name) / "legacy.orf"
        key_path = Path(self.temp_dir.name) / "custom.key"
        key, public_key = generate_key_pair()
        profile = ORFProfile.create("Legacy User", public_key, "device-legacy")
        cli._apply_signed_event(
            profile, key, EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        cli.save_profile(profile_path, profile)
        save_private_key(key_path, key, passphrase="test-passphrase")
        with patch("open_recommender.cli.send_json", side_effect=self._send_json):
            for _ in range(2):
                code, _, stderr = self._run_local_cli(
                    "sync-push", str(profile_path), self.server,
                    "--key-path", str(key_path), "--key-passphrase", "test-passphrase",
                )
                self.assertEqual(code, 0, stderr)
        local = cli.load_profile(profile_path)
        stored = self.app.state.store.get_profile(profile.profile_id)
        self.assertEqual(len(local.event_log), 2)
        self.assertEqual(len(stored.event_log), 2)
        self.assertEqual(stored.display_name, "Legacy User")
        self.assertEqual(stored.topics["orf:technology/python"].weight, 0.9)

    def test_cli_can_revoke_and_delete_without_removing_local_files(self) -> None:
        created = self.client.post(
            f"/profiles/{self.profile.profile_id}/site-access-requests",
            json={"site_id": "open-news-demo", "purpose": "Test CLI", "requested_scopes": ["profile.read"]},
        ).json()
        request_id = created["access_request"]["request_id"]
        _, approved = self._run_cli(
            "site-access-request-approve", request_id, self.server,
            "--profile-path", str(self.profile_path),
        )
        code, revoked = self._run_cli(
            "site-grant-revoke", approved["grant"]["grant_id"], self.server,
            "--profile-path", str(self.profile_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("revoked_at", revoked["grant"])
        with patch("open_recommender.cli.send_json") as sender:
            code, _, stderr = self._run_local_cli("profile-delete", str(self.profile_path), self.server)
            self.assertEqual(code, 1)
            self.assertIn("--confirm", stderr)
            sender.assert_not_called()
        code, deleted = self._run_cli("profile-delete", str(self.profile_path), self.server, "--confirm")
        self.assertEqual(code, 0)
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(self.app.state.store.get_profile(self.profile.profile_id))
        self.assertTrue(self.profile_path.exists())
        self.assertTrue(self.profile_path.with_suffix(".orf.key").exists())

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
        self.assertEqual(len(seeded_profile.event_log), output["seed"]["total_events_added"] + 1)
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
        self.assertEqual(len(seeded_profile.event_log), seed["total_events_added"] + 1)
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
