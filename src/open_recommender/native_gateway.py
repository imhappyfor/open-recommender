"""Verify the native gateway's separate v1 site-login contract, not hosted grants.

Call from a site's backend with values taken from its own outstanding challenge.
After verification, consume that challenge atomically with creating the site session.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crypto import canonical_json

VERSION = "orf-native-connect-v1"
SIGNATURE_PREFIX = b"ORF native connect v1\n"
PAYLOAD_FIELDS = {"version", "audience", "nonce", "subject", "public_key", "issued_at", "expires_at"}


def _decode(value: Any, length: int) -> bytes:
    if not isinstance(value, str) or len(value) != (length * 8 + 5) // 6 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid native proof encoding.")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid native proof encoding.") from error
    if len(decoded) != length or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise ValueError("Invalid native proof encoding.")
    return decoded


def verify_native_login(proof: dict[str, Any], *, origin: str, nonce: str,
                        expires_at: int, now: int | None = None) -> str:
    """Return the site-specific subject; this does not consume a nonce or create a session."""
    now = int(time.time()) if now is None else now
    if type(now) is not int or type(expires_at) is not int:
        raise ValueError("Expected integer challenge times.")
    if not isinstance(proof, dict) or set(proof) != {"payload", "signature"}:
        raise ValueError("Invalid native proof fields.")
    payload = proof["payload"]
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ValueError("Invalid native payload fields.")
    if any(not isinstance(payload[field], str) for field in PAYLOAD_FIELDS - {"issued_at", "expires_at"}):
        raise ValueError("Invalid native payload types.")
    if any(type(payload[field]) is not int for field in ("issued_at", "expires_at")):
        raise ValueError("Invalid native proof times.")
    if payload["version"] != VERSION or payload["audience"] != origin or payload["nonce"] != nonce:
        raise ValueError("Native proof does not match this website's challenge.")
    _decode(nonce, 32)
    if (payload["expires_at"] != expires_at or not now < expires_at
            or not now - 300 <= payload["issued_at"] <= now + 30
            or not 0 < expires_at - payload["issued_at"] <= 300):
        raise ValueError("Native proof has expired or has invalid timing.")
    public_key = _decode(payload["public_key"], 32)
    subject = "orf:site:" + hashlib.sha256(public_key).hexdigest()[:32]
    if payload["subject"] != subject:
        raise ValueError("Native subject does not match its site key.")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _decode(proof["signature"], 64), SIGNATURE_PREFIX + canonical_json(payload))
    except InvalidSignature as error:
        raise ValueError("Invalid native login signature.") from error
    return subject
