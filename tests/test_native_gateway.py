from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from open_recommender.crypto import canonical_json, generate_key_pair
from open_recommender.native_gateway import SIGNATURE_PREFIX, verify_native_login


class NativeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.proof = json.loads((Path(__file__).parent / "fixtures/native-login-proof.json").read_text())

    def verify(self, proof=None, **overrides):
        args = dict(origin="https://news.example", nonce=self.proof["payload"]["nonce"],
                    expires_at=1800000120, now=1800000000)
        args.update(overrides)
        return verify_native_login(self.proof if proof is None else proof, **args)

    def test_shared_swift_node_python_proof_and_boundaries(self):
        self.assertEqual(self.verify(), "orf:site:56475aa75463474c0285df5dbf2bcab7")
        for overrides in [dict(origin="https://shop.example"), dict(nonce="A" * 43),
                          dict(expires_at=1800000121), dict(now=1800000120), dict(now=1799999900)]:
            with self.assertRaises(ValueError): self.verify(**overrides)
        for name, value in [("subject", "orf:site:other"), ("issued_at", True),
                            ("expires_at", 1800000120.0), ("version", "unknown"),
                            ("public_key", self.proof["payload"]["public_key"] + "=")]:
            proof = copy.deepcopy(self.proof)
            proof["payload"][name] = value
            with self.assertRaises(ValueError): self.verify(proof)
        for name, value in [("private_key", "bad"), ("profile_id", "root")]:
            proof = copy.deepcopy(self.proof)
            proof["payload"][name] = value
            with self.assertRaises(ValueError): self.verify(proof)
        proof = copy.deepcopy(self.proof)
        proof["signature"] = "A" * 86
        with self.assertRaises(ValueError): self.verify(proof)

    def test_demo_browser_session_csrf_and_atomic_nonce_consumption(self):
        import base64
        import hashlib
        import re
        import time
        module_path = Path(__file__).resolve().parents[1] / "examples/native_site.py"
        spec = importlib.util.spec_from_file_location("native_site_example", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        create_native_site_app = module.create_native_site_app
        origin = "http://127.0.0.1:8766"
        client = TestClient(create_native_site_app(), base_url=origin)
        other = TestClient(create_native_site_app(), base_url=origin)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        csrf = re.search(r'name="orf-csrf" content="([^"]+)"', response.text).group(1)
        headers = {"Origin": origin, "X-ORF-Demo-CSRF": csrf}
        self.assertEqual(client.post("/challenge").status_code, 403)
        challenge = client.post("/challenge", headers=headers).json()
        key, encoded = generate_key_pair()
        public_key = encoded.rstrip("=")
        payload = dict(version="orf-native-connect-v1", audience=origin, nonce=challenge["nonce"],
            subject="orf:site:" + hashlib.sha256(base64.urlsafe_b64decode(encoded)).hexdigest()[:32],
            public_key=public_key, issued_at=int(time.time()), expires_at=challenge["expires_at"])
        proof = {"payload": payload, "signature": base64.urlsafe_b64encode(
            key.sign(SIGNATURE_PREFIX + canonical_json(payload))).decode().rstrip("=")}
        self.assertEqual(other.post("/verify", json={"proof": proof}, headers=headers).status_code, 401)
        self.assertEqual(client.post("/verify", json={"proof": proof}, headers={**headers, "Origin": "https://evil.example"}).status_code, 403)
        bad = copy.deepcopy(proof); bad["payload"]["audience"] = "https://other.example"
        self.assertEqual(client.post("/verify", json={"proof": bad}, headers=headers).status_code, 400)
        verified = client.post("/verify", json={"proof": proof}, headers=headers)
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["site_subject"], payload["subject"])
        self.assertNotIn("profile_id", verified.text)
        self.assertEqual(client.post("/verify", json={"proof": proof}, headers=headers).status_code, 400)
        self.assertEqual(client.post("/verify", content=b" " * 5000, headers=headers).status_code, 413)
        remote = TestClient(create_native_site_app(), base_url=origin, client=("198.51.100.10", 1))
        self.assertEqual(remote.get("/").status_code, 403)
        self.assertEqual(client.get("/", headers={"Host": "evil.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
