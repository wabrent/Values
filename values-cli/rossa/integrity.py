"""
Tamper-evidence for `.ethos` files.

Two layers, deliberately separate:

* **Digest** (always available, stdlib only). Detects that a file changed after
  it was written. Does not prove *who* wrote it.
* **Signature** (optional, needs `cryptography`). Ed25519 over the raw digest
  bytes. Proves the holder of a private key produced this exact content.

Honest scope, restated from SPEC.md §5: this layer detects edits at rest and
substitution by a third party. It does **not** and cannot prove a runtime
actually honoured the file. That is what `probe.py` is for, and even that
returns evidence, not proof.

Failure mode policy: verification returns a status object rather than raising.
A person whose file fails verification needs to see the file and the reason,
not a traceback.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .schema import EthosFile, canonical_bytes, compute_digest, DIGEST_ALGORITHM

# `cryptography` is optional. Digest integrity works without it.
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover - environment-dependent
    _HAS_CRYPTO = False


class IntegrityStatus(str, Enum):
    """Outcome of verification. Ordered from best to worst."""

    SIGNED_VALID = "signed_valid"
    """Digest matches and the Ed25519 signature verifies."""

    DIGEST_VALID = "digest_valid"
    """Digest matches. No signature present."""

    UNSIGNED = "unsigned"
    """No digest recorded. Nothing to check."""

    DIGEST_MISMATCH = "digest_mismatch"
    """Content changed after the digest was written."""

    SIGNATURE_INVALID = "signature_invalid"
    """Digest matches but the signature does not verify."""

    SIGNATURE_UNCHECKED = "signature_unchecked"
    """Signature present but `cryptography` is unavailable to verify it."""

    @property
    def ok(self) -> bool:
        """Whether the file can be trusted as unmodified."""
        return self in (IntegrityStatus.SIGNED_VALID, IntegrityStatus.DIGEST_VALID)


@dataclass
class VerificationResult:
    status: IntegrityStatus
    expected_digest: str
    """Digest recorded in the file."""

    actual_digest: str
    """Digest recomputed from the content."""

    message: str
    signer: Optional[str] = None
    """Signing public key, hex, if a signature was present."""

    @property
    def ok(self) -> bool:
        return self.status.ok

    def __str__(self) -> str:
        mark = "OK" if self.ok else "FAIL"
        return f"[{mark}] {self.status.value}: {self.message}"


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify(ef: EthosFile) -> VerificationResult:
    """Check digest and, if present, signature. Never raises."""
    recorded = (ef.integrity.digest or "").strip().lower()
    actual = compute_digest(ef)

    if not recorded:
        return VerificationResult(
            status=IntegrityStatus.UNSIGNED,
            expected_digest="",
            actual_digest=actual,
            message=(
                "no digest recorded; edits to this file cannot be detected. "
                "Run `ethos sign` to add one."
            ),
        )

    if ef.integrity.algorithm != DIGEST_ALGORITHM:
        return VerificationResult(
            status=IntegrityStatus.DIGEST_MISMATCH,
            expected_digest=recorded,
            actual_digest=actual,
            message=(
                f"unsupported digest algorithm {ef.integrity.algorithm!r}; "
                f"this implementation only computes {DIGEST_ALGORITHM!r}"
            ),
        )

    # Constant-time comparison is not required here — the digest is public — but
    # it costs nothing and avoids setting a bad example.
    if not _consteq(recorded, actual):
        return VerificationResult(
            status=IntegrityStatus.DIGEST_MISMATCH,
            expected_digest=recorded,
            actual_digest=actual,
            message=(
                "content does not match the recorded digest. The file was "
                "modified after signing. Review the changes before using it."
            ),
        )

    sig = ef.integrity.signature
    if not sig:
        return VerificationResult(
            status=IntegrityStatus.DIGEST_VALID,
            expected_digest=recorded,
            actual_digest=actual,
            message="digest matches; file is unmodified since it was written",
        )

    if sig.get("scheme") != "ed25519":
        return VerificationResult(
            status=IntegrityStatus.SIGNATURE_INVALID,
            expected_digest=recorded,
            actual_digest=actual,
            message=f"unsupported signature scheme {sig.get('scheme')!r}",
            signer=sig.get("public_key"),
        )

    if not _HAS_CRYPTO:
        return VerificationResult(
            status=IntegrityStatus.SIGNATURE_UNCHECKED,
            expected_digest=recorded,
            actual_digest=actual,
            message=(
                "digest matches, but the Ed25519 signature could not be checked: "
                "install `cryptography` to verify it"
            ),
            signer=sig.get("public_key"),
        )

    try:
        pub_bytes = binascii.unhexlify(sig["public_key"])
        sig_bytes = binascii.unhexlify(sig["value"])
        message = binascii.unhexlify(actual)  # sign the raw digest, per SPEC §2
        Ed25519PublicKey.from_public_bytes(pub_bytes).verify(sig_bytes, message)
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        return VerificationResult(
            status=IntegrityStatus.SIGNATURE_INVALID,
            expected_digest=recorded,
            actual_digest=actual,
            message=f"signature does not verify: {exc.__class__.__name__}",
            signer=sig.get("public_key"),
        )

    return VerificationResult(
        status=IntegrityStatus.SIGNED_VALID,
        expected_digest=recorded,
        actual_digest=actual,
        message="digest matches and signature verifies",
        signer=sig["public_key"],
    )


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


# ---------------------------------------------------------------------------
# Sign
# ---------------------------------------------------------------------------


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair. Returns `(private_hex, public_hex)`.

    The private key never leaves the caller. `ethos keygen` writes it to a file
    the user controls; nothing in this package transmits it anywhere.
    """
    if not _HAS_CRYPTO:
        raise RuntimeError(
            "signing requires the `cryptography` package: pip install cryptography"
        )
    from cryptography.hazmat.primitives import serialization

    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_raw.hex(), pub_raw.hex()


def sign(ef: EthosFile, private_key_hex: Optional[str] = None) -> EthosFile:
    """Recompute the digest and, when a key is supplied, attach a signature.

    Mutates and returns `ef`. Signing always recomputes the digest first so a
    signature can never attest to stale content.
    """
    ef.integrity.algorithm = DIGEST_ALGORITHM
    ef.integrity.digest = compute_digest(ef)

    if private_key_hex is None:
        ef.integrity.signature = None
        return ef

    if not _HAS_CRYPTO:
        raise RuntimeError(
            "signing requires the `cryptography` package: pip install cryptography"
        )

    try:
        priv_raw = binascii.unhexlify(private_key_hex.strip())
    except binascii.Error as exc:
        raise ValueError(f"private key is not valid hex: {exc}") from exc

    if len(priv_raw) != 32:
        raise ValueError(
            f"Ed25519 private key must be 32 bytes (64 hex chars); got {len(priv_raw)}"
        )

    priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
    from cryptography.hazmat.primitives import serialization

    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    message = binascii.unhexlify(ef.integrity.digest)
    signature = priv.sign(message)

    ef.integrity.signature = {
        "scheme": "ed25519",
        "public_key": pub_raw.hex(),
        "value": signature.hex(),
    }
    return ef


def crypto_available() -> bool:
    """Whether Ed25519 signing and verification are available."""
    return _HAS_CRYPTO
