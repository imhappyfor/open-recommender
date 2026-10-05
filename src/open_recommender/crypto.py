from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

import rfc8785

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


JCS_SIGNATURE_ENCODING = "jcs-rfc8785"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object fields are not allowed.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constants are not allowed.")


def load_json(data: str | bytes | bytearray) -> Any:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return json.loads(data, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
        "utf-8"
    )


def _jcs_input(value: Any) -> Any:
    # JSON.stringify emits large integral doubles as integer tokens below 1e21.
    # Accept those only when conversion preserves the exact mathematical value.
    if type(value) is int and abs(value) > 2**53 - 1:
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError("JCS numbers must fit binary64; use strings for large exact integers.") from error
        if not math.isfinite(number) or number != value:
            raise ValueError("JCS numbers must fit binary64; use strings for large exact integers.")
        return number
    if isinstance(value, dict):
        return {key: _jcs_input(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jcs_input(item) for item in value]
    return value


def canonical_signed_json(payload: dict[str, Any]) -> bytes:
    """An explicit, signed marker selects JCS; absence preserves legacy bytes."""
    if "signature_encoding" not in payload:
        return canonical_json(payload)
    if payload["signature_encoding"] != JCS_SIGNATURE_ENCODING:
        raise ValueError("Unsupported signature encoding.")
    return rfc8785.dumps(_jcs_input(payload))


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


def write_private_file(path: str | Path, data: bytes, *, overwrite: bool = True) -> Path:
    """Stage owner-only bytes beside the target; never truncate an existing file."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"Refusing to save through a symbolic link: {target}")
    staged = tempfile.NamedTemporaryFile(dir=target.parent, prefix=".orf-save-", suffix=".orf", delete=False)
    temporary = Path(staged.name)
    try:
        with staged:
            staged.write(data)
            staged.flush()
            os.fsync(staged.fileno())
        # ponytail: atomic per file; use a journal for multi-file or power-loss recovery.
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)  # Atomic no-clobber publication on the same filesystem.
    finally:
        temporary.unlink(missing_ok=True)
    return target


def save_private_key(
    path: str | Path, private_key: Ed25519PrivateKey, passphrase: str | None = None,
    *, overwrite: bool = True,
) -> Path:
    return write_private_file(path, serialize_private_key(private_key, passphrase=passphrase), overwrite=overwrite)


def private_key_public_key_b64(private_key: Ed25519PrivateKey) -> str:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return encode_bytes(public_key)


def sign_payload(payload: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    signature = private_key.sign(canonical_signed_json(payload))
    return encode_bytes(signature)


def verify_signature(payload: dict[str, Any], signature_b64: str, public_key_b64: str) -> bool:
    public_key = Ed25519PublicKey.from_public_bytes(decode_bytes(public_key_b64))
    try:
        public_key.verify(decode_bytes(signature_b64), canonical_signed_json(payload))
    except InvalidSignature as error:
        raise ValueError("Signature verification failed.") from error
    return True
