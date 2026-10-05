import hashlib
import json
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from open_recommender.crypto import canonical_json, fingerprint_public_key, sign_payload, verify_signature


class SigningVectorTests(unittest.TestCase):
    def test_shared_challenge_vectors(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "signing-vectors.json").read_text())
        self.assertEqual(fingerprint_public_key(fixture["public_key"]), fixture["profile_id"])
        test_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        for vector in fixture["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(canonical_json(vector["payload"]).hex(), vector["canonical_utf8_hex"])
                self.assertEqual(sign_payload(vector["payload"], test_key), vector["signature"])
                self.assertTrue(verify_signature(vector["payload"], vector["signature"], fixture["public_key"]))
        digest = hashlib.sha256(canonical_json(fixture["owner_action"])).hexdigest()
        self.assertEqual(fixture["vectors"][1]["payload"]["challenge_type"], f"owner-deny:{digest}")


if __name__ == "__main__":
    unittest.main()
