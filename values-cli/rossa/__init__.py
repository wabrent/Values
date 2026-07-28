"""
ROSSA — Your AI, Your Values. A portable, tamper-evident values file for AI systems.

Response to Sentient Foundation RFP Part Two #08: "Your AI, Your Values."

The premise: a handful of labs currently decide how every major model behaves.
The alternative is not retraining — it is a file that a person owns, reads,
edits, carries between models, and can prove is being honoured.

Public API:

    from rossa import EthosFile, compile_target, verify, probe

Everything here is stdlib-only. Ed25519 signing uses `cryptography` if present
and degrades to digest-only integrity if it is not.
"""

__version__ = "1.0.0"
__spec_version__ = "1.0"

from .schema import (
    EthosFile,
    Value,
    Directives,
    Subject,
    Integrity,
    Firmness,
    ValidationError,
    canonical_bytes,
    compute_digest,
)
from .compiler import compile_target, TARGETS
from .integrity import verify, sign, generate_keypair, IntegrityStatus
from .dilemmas import DILEMMAS, Dilemma, dilemmas_for_axes

__all__ = [
    "__version__",
    "__spec_version__",
    "EthosFile",
    "Value",
    "Directives",
    "Subject",
    "Integrity",
    "Firmness",
    "ValidationError",
    "canonical_bytes",
    "compute_digest",
    "compile_target",
    "TARGETS",
    "verify",
    "sign",
    "generate_keypair",
    "IntegrityStatus",
    "DILEMMAS",
    "Dilemma",
    "dilemmas_for_axes",
]
