from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def encode_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def decode_bytes(data: str) -> bytes:
    normalized = data.strip()
    padding = (-len(normalized)) % 4
    if padding:
        normalized += "=" * padding
    return base64.urlsafe_b64decode(normalized.encode("ascii"))


def generate_key_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, encode_bytes(public_key)


def fingerprint_public_key(public_key_b64: str) -> str:
    digest = hashlib.sha256(decode_bytes(public_key_b64)).hexdigest()[:32]
    return f"orf:profile:{digest}"


def serialize_private_key(private_key: Ed25519PrivateKey, passphrase: str | None = None) -> bytes:
    encryption = serialization.NoEncryption()
    if passphrase:
        encryption = serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def load_private_key(path: str | Path, passphrase: str | None = None) -> Ed25519PrivateKey:
    key_bytes = Path(path).read_bytes()
    return load_private_key_bytes(key_bytes, passphrase=passphrase)


def load_private_key_bytes(key_bytes: bytes, passphrase: str | None = None) -> Ed25519PrivateKey:
    password = passphrase.encode("utf-8") if passphrase else None
    return serialization.load_pem_private_key(key_bytes, password=password)


def save_private_key(
    path: str | Path, private_key: Ed25519PrivateKey, passphrase: str | None = None
) -> Path:
    key_path = Path(path)
    key_path.write_bytes(serialize_private_key(private_key, passphrase=passphrase))
    return key_path


def private_key_public_key_b64(private_key: Ed25519PrivateKey) -> str:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return encode_bytes(public_key)


def sign_payload(payload: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    signature = private_key.sign(canonical_json(payload))
    return encode_bytes(signature)


def verify_signature(payload: dict[str, Any], signature_b64: str, public_key_b64: str) -> bool:
    public_key = Ed25519PublicKey.from_public_bytes(decode_bytes(public_key_b64))
    try:
        public_key.verify(decode_bytes(signature_b64), canonical_json(payload))
    except InvalidSignature as error:
        raise ValueError("Signature verification failed.") from error
    return True
