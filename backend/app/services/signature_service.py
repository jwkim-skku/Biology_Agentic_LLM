from __future__ import annotations

import base64
import hmac
import importlib.util
from hashlib import sha256
from typing import Any

from app.config import get_settings


def signing_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "hmac": {
            "signing_enabled": settings.artifact_signing_enabled,
            "verification_enabled": settings.artifact_signing_enabled,
            "algorithm": "HMAC-SHA256",
            "key_id": settings.artifact_signing_key_id if settings.artifact_signing_enabled else None,
        },
        "ed25519": {
            "signing_enabled": settings.artifact_asymmetric_signing_enabled,
            "verification_enabled": settings.artifact_asymmetric_verification_enabled,
            "driver_available": _cryptography_available(),
            "algorithm": "Ed25519",
            "key_id": settings.artifact_ed25519_key_id
            if (settings.artifact_asymmetric_signing_enabled or settings.artifact_asymmetric_verification_enabled)
            else None,
        },
    }


def signatures_for_hash(payload_hash: str, *, signed_field: str) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    hmac_signature = _hmac_signature(payload_hash, signed_field=signed_field)
    if hmac_signature:
        signatures.append(hmac_signature)
    ed25519_signature = _ed25519_signature(payload_hash, signed_field=signed_field)
    if ed25519_signature:
        signatures.append(ed25519_signature)
    return signatures


def verify_payload_signatures(
    payload_hash: str,
    signatures: list[dict[str, Any]],
    *,
    signed_field: str,
) -> dict[str, Any]:
    settings = get_settings()
    if not signatures:
        messages = []
        if settings.artifact_signing_enabled:
            messages.append(f"{signed_field} is unsigned although ARTIFACT_SIGNING_KEY is configured.")
        if settings.artifact_asymmetric_signing_enabled and not _cryptography_available():
            messages.append(f"{signed_field} has no Ed25519 signature because the cryptography package is unavailable.")
        elif settings.artifact_asymmetric_verification_enabled:
            messages.append(f"{signed_field} has no Ed25519 signature although ARTIFACT_ED25519_PUBLIC_KEY is configured.")
        return {"status": "warning" if messages else "unsigned", "messages": messages, "signatures": []}

    errors: list[str] = []
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    for signature in signatures:
        algorithm = signature.get("algorithm")
        if algorithm == "HMAC-SHA256":
            result = _verify_hmac_signature(payload_hash, signature, signed_field=signed_field)
        elif algorithm == "Ed25519":
            result = _verify_ed25519_signature(payload_hash, signature, signed_field=signed_field)
        else:
            result = {"status": "fail", "messages": [f"Unsupported signature algorithm: {algorithm}."]}
        results.append({"algorithm": algorithm, "key_id": signature.get("key_id"), **result})
        if result["status"] == "fail":
            errors.extend(result["messages"])
        elif result["status"] == "warning":
            warnings.extend(result["messages"])

    return {
        "status": "fail" if errors else "warning" if warnings else "verified",
        "messages": errors + warnings,
        "signatures": results,
    }


def _hmac_signature(payload_hash: str, *, signed_field: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.artifact_signing_enabled:
        return None
    signature = hmac.new(settings.artifact_signing_key.encode("utf-8"), payload_hash.encode("ascii"), sha256).hexdigest()
    return {
        "algorithm": "HMAC-SHA256",
        "key_id": settings.artifact_signing_key_id,
        "signed_field": signed_field,
        "signature": signature,
    }


def _verify_hmac_signature(payload_hash: str, signature: dict[str, Any], *, signed_field: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.artifact_signing_enabled:
        return {"status": "warning", "messages": ["HMAC signature is present, but ARTIFACT_SIGNING_KEY is not configured."]}
    if signature.get("key_id") != settings.artifact_signing_key_id:
        return {"status": "fail", "messages": ["HMAC signature key_id does not match configured verifier key id."]}
    if signature.get("signed_field") != signed_field:
        return {"status": "fail", "messages": [f"HMAC signature signed_field is not {signed_field}."]}
    expected = _hmac_signature(payload_hash, signed_field=signed_field)
    if not hmac.compare_digest(str(signature.get("signature") or ""), str((expected or {}).get("signature") or "")):
        return {"status": "fail", "messages": ["HMAC signature verification failed."]}
    return {"status": "verified", "messages": []}


def _ed25519_signature(payload_hash: str, *, signed_field: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.artifact_asymmetric_signing_enabled:
        return None
    if not _cryptography_available():
        return None
    private_key = _load_ed25519_private_key(settings.artifact_ed25519_private_key)
    signature = private_key.sign(payload_hash.encode("ascii"))
    return {
        "algorithm": "Ed25519",
        "key_id": settings.artifact_ed25519_key_id,
        "signed_field": signed_field,
        "signature": base64.b64encode(signature).decode("ascii"),
        "encoding": "base64",
    }


def _verify_ed25519_signature(payload_hash: str, signature: dict[str, Any], *, signed_field: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.artifact_asymmetric_verification_enabled:
        return {"status": "warning", "messages": ["Ed25519 signature is present, but ARTIFACT_ED25519_PUBLIC_KEY is not configured."]}
    if not _cryptography_available():
        return {"status": "fail", "messages": ["Ed25519 verification requires the cryptography package."]}
    if signature.get("key_id") != settings.artifact_ed25519_key_id:
        return {"status": "fail", "messages": ["Ed25519 signature key_id does not match configured verifier key id."]}
    if signature.get("signed_field") != signed_field:
        return {"status": "fail", "messages": [f"Ed25519 signature signed_field is not {signed_field}."]}
    try:
        public_key = _verification_public_key(settings.artifact_ed25519_public_key, settings.artifact_ed25519_private_key)
        public_key.verify(base64.b64decode(str(signature.get("signature") or "")), payload_hash.encode("ascii"))
    except Exception as exc:  # noqa: BLE001 - invalid signatures surface as verification failure.
        return {"status": "fail", "messages": [f"Ed25519 signature verification failed: {exc}"]}
    return {"status": "verified", "messages": []}


def _load_ed25519_private_key(encoded: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if "BEGIN" in encoded:
        return serialization.load_pem_private_key(encoded.encode("utf-8"), password=None)
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded))


def _load_ed25519_public_key(encoded: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if "BEGIN" in encoded:
        return serialization.load_pem_public_key(encoded.encode("utf-8"))
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded))


def _verification_public_key(public_key: str, private_key: str):
    if public_key:
        return _load_ed25519_public_key(public_key)
    return _load_ed25519_private_key(private_key).public_key()


def _cryptography_available() -> bool:
    return importlib.util.find_spec("cryptography") is not None
