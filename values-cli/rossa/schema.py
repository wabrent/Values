"""
Schema, validation, and canonical serialisation for the `.ethos` format.

Two responsibilities, deliberately kept together because they must not drift
apart:

1. Parse and validate a `.ethos` file, rejecting malformed input with a reason
   a human can act on.
2. Produce the canonical byte serialisation defined in SPEC.md §2, so that a
   digest computed here matches one computed by any other conforming
   implementation in any language.

Design note: validation errors accumulate rather than raising on the first
problem. A person editing their own values file deserves the full list, not a
game of whack-a-mole.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

SPEC_VERSION = "1.0"
DIGEST_ALGORITHM = "sha256-canonical-json-v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when a `.ethos` file does not conform to the spec.

    Carries every problem found, not just the first, so the caller can show a
    complete list.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{len(problems)} problem(s) found:\n{joined}")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Firmness(str, Enum):
    """How hard a value is held. See SPEC.md §1.3.

    ABSOLUTE is a hard line the model must not cross even on request.
    STRONG is a default that yields only to an explicit in-conversation override.
    PREFERENCE is a lean that shapes tone and yields to context freely.
    """

    ABSOLUTE = "absolute"
    STRONG = "strong"
    PREFERENCE = "preference"

    @property
    def rank(self) -> int:
        """Sort order for rendering: absolutes first."""
        return {"absolute": 0, "strong": 1, "preference": 2}[self.value]


TONES = ("plain", "warm", "terse", "socratic", "formal")
VERBOSITIES = ("minimal", "balanced", "thorough")
DISAGREEMENTS = ("voice_it", "defer", "flag_once")
REFUSAL_STYLES = ("explain", "terse", "redirect")
UNCERTAINTIES = ("admit", "hedge", "commit")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Subject:
    """Who the file speaks for. Everything but `label` may be omitted."""

    label: str
    locale: Optional[str] = None
    created: Optional[str] = None
    revision: int = 1
    notes: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"label": self.label, "revision": self.revision}
        if self.locale:
            out["locale"] = self.locale
        if self.created:
            out["created"] = self.created
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any], problems: list[str]) -> "Subject":
        label = d.get("label")
        if not isinstance(label, str) or not label.strip():
            problems.append("subject.label must be a non-empty string")
            label = "unknown"

        revision = d.get("revision", 1)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            problems.append("subject.revision must be an integer >= 1")
            revision = 1

        locale = d.get("locale")
        if locale is not None and not isinstance(locale, str):
            problems.append("subject.locale must be a string (BCP-47) if present")
            locale = None

        return cls(
            label=label,
            locale=locale,
            created=d.get("created") if isinstance(d.get("created"), str) else None,
            revision=revision,
            notes=d.get("notes") if isinstance(d.get("notes"), str) else None,
        )


@dataclass
class Value:
    """A single held value. See SPEC.md §1.3."""

    id: str
    statement: str
    weight: float
    firmness: Firmness
    axis: Optional[str] = None
    derived_from: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "weight": self.weight,
            "firmness": self.firmness.value,
        }
        if self.axis:
            out["axis"] = self.axis
        if self.derived_from:
            out["derived_from"] = list(self.derived_from)
        return out

    @classmethod
    def from_dict(cls, d: Any, index: int, problems: list[str]) -> Optional["Value"]:
        where = f"values[{index}]"
        if not isinstance(d, dict):
            problems.append(f"{where} must be an object")
            return None

        vid = d.get("id")
        if not isinstance(vid, str) or not vid.strip():
            problems.append(f"{where}.id must be a non-empty string")
            return None
        if vid != vid.lower() or " " in vid:
            problems.append(f"{where}.id '{vid}' should be lowercase snake_case")

        statement = d.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            problems.append(f"{where}.statement must be a non-empty string")
            return None

        weight = d.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            problems.append(f"{where}.weight must be a number in [0.0, 1.0]")
            weight = 0.5
        elif not (0.0 <= float(weight) <= 1.0):
            problems.append(f"{where}.weight is {weight}; must be in [0.0, 1.0]")
            weight = min(1.0, max(0.0, float(weight)))

        raw_firmness = d.get("firmness")
        try:
            firmness = Firmness(raw_firmness)
        except ValueError:
            problems.append(
                f"{where}.firmness must be one of "
                f"{', '.join(f.value for f in Firmness)} (got {raw_firmness!r})"
            )
            firmness = Firmness.PREFERENCE

        derived = d.get("derived_from", [])
        if not isinstance(derived, list) or not all(isinstance(x, str) for x in derived):
            problems.append(f"{where}.derived_from must be an array of strings")
            derived = []

        return cls(
            id=vid,
            statement=statement.strip(),
            weight=round(float(weight), 4),
            firmness=firmness,
            axis=d.get("axis") if isinstance(d.get("axis"), str) else None,
            derived_from=derived,
        )


@dataclass
class Directives:
    """Behaviour that is not a value but is still the subject's call."""

    tone: str = "plain"
    verbosity: str = "balanced"
    disagreement: str = "voice_it"
    refusal_style: str = "explain"
    uncertainty: str = "admit"
    custom: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tone": self.tone,
            "verbosity": self.verbosity,
            "disagreement": self.disagreement,
            "refusal_style": self.refusal_style,
            "uncertainty": self.uncertainty,
        }
        if self.custom:
            out["custom"] = list(self.custom)
        return out

    @classmethod
    def from_dict(cls, d: Any, problems: list[str]) -> "Directives":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            problems.append("directives must be an object")
            return cls()

        def pick(key: str, allowed: Iterable[str], default: str) -> str:
            val = d.get(key, default)
            if val not in allowed:
                problems.append(
                    f"directives.{key} must be one of {', '.join(allowed)} (got {val!r})"
                )
                return default
            return val

        custom = d.get("custom", [])
        if not isinstance(custom, list) or not all(isinstance(x, str) for x in custom):
            problems.append("directives.custom must be an array of strings")
            custom = []

        return cls(
            tone=pick("tone", TONES, "plain"),
            verbosity=pick("verbosity", VERBOSITIES, "balanced"),
            disagreement=pick("disagreement", DISAGREEMENTS, "voice_it"),
            refusal_style=pick("refusal_style", REFUSAL_STYLES, "explain"),
            uncertainty=pick("uncertainty", UNCERTAINTIES, "admit"),
            custom=custom,
        )


@dataclass
class Integrity:
    """Digest and optional signature. See SPEC.md §1.5."""

    algorithm: str = DIGEST_ALGORITHM
    digest: str = ""
    signature: Optional[dict[str, str]] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"algorithm": self.algorithm, "digest": self.digest}
        if self.signature:
            out["signature"] = dict(self.signature)
        return out

    @classmethod
    def from_dict(cls, d: Any, problems: list[str]) -> "Integrity":
        if d is None:
            return cls()
        if not isinstance(d, dict):
            problems.append("integrity must be an object")
            return cls()

        algorithm = d.get("algorithm", DIGEST_ALGORITHM)
        if algorithm != DIGEST_ALGORITHM:
            problems.append(
                f"integrity.algorithm must be {DIGEST_ALGORITHM!r} (got {algorithm!r})"
            )

        digest = d.get("digest", "")
        if not isinstance(digest, str):
            problems.append("integrity.digest must be a hex string")
            digest = ""

        sig = d.get("signature")
        if sig is not None:
            if not isinstance(sig, dict):
                problems.append("integrity.signature must be an object")
                sig = None
            else:
                missing = [k for k in ("scheme", "public_key", "value") if k not in sig]
                if missing:
                    problems.append(
                        f"integrity.signature missing key(s): {', '.join(missing)}"
                    )
                    sig = None

        return cls(algorithm=algorithm, digest=digest, signature=sig)


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


@dataclass
class EthosFile:
    """A parsed, validated `.ethos` file.

    `values` order is semantic: earlier entries win conflicts (SPEC.md §1.3).
    Never sort this list in place — use `values_by_firmness()` for rendering.
    """

    subject: Subject
    values: list[Value]
    directives: Directives = field(default_factory=Directives)
    integrity: Integrity = field(default_factory=Integrity)
    ethos_version: str = SPEC_VERSION

    # -- construction -------------------------------------------------------

    @classmethod
    def new(cls, label: str, locale: Optional[str] = None) -> "EthosFile":
        """Create an empty file with a current timestamp."""
        return cls(
            subject=Subject(
                label=label,
                locale=locale,
                created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                revision=1,
            ),
            values=[],
        )

    @classmethod
    def from_dict(cls, raw: Any, *, strict: bool = True) -> "EthosFile":
        """Parse a dict into an EthosFile.

        With `strict=True` (default) any problem raises ValidationError.
        With `strict=False` the file is repaired where possible — used by the
        editor UI so a person is never locked out of their own file.
        """
        problems: list[str] = []

        if not isinstance(raw, dict):
            raise ValidationError(["top level must be a JSON object"])

        version = raw.get("ethos_version")
        if version != SPEC_VERSION:
            major = str(version).split(".")[0] if version else "?"
            if major != SPEC_VERSION.split(".")[0]:
                problems.append(
                    f"ethos_version {version!r} is not supported by this "
                    f"implementation (expects {SPEC_VERSION!r})"
                )

        subject = Subject.from_dict(
            raw.get("subject") if isinstance(raw.get("subject"), dict) else {}, problems
        )

        raw_values = raw.get("values")
        if not isinstance(raw_values, list):
            problems.append("values must be an array")
            raw_values = []
        if len(raw_values) == 0:
            problems.append("values must contain at least one entry")

        values: list[Value] = []
        seen: set[str] = set()
        for i, item in enumerate(raw_values):
            v = Value.from_dict(item, i, problems)
            if v is None:
                continue
            if v.id in seen:
                problems.append(f"duplicate value id {v.id!r} at values[{i}]")
                continue
            seen.add(v.id)
            values.append(v)

        directives = Directives.from_dict(raw.get("directives"), problems)
        integrity = Integrity.from_dict(raw.get("integrity"), problems)

        # A file of nothing but absolutes cannot function — warn loudly.
        absolutes = [v for v in values if v.firmness is Firmness.ABSOLUTE]
        if values and len(absolutes) == len(values) and len(values) > 3:
            problems.append(
                f"all {len(values)} values are 'absolute'; a file with no room to "
                "yield cannot resolve real dilemmas (SPEC.md §1.3)"
            )

        if problems and strict:
            raise ValidationError(problems)

        return cls(
            subject=subject,
            values=values,
            directives=directives,
            integrity=integrity,
            ethos_version=SPEC_VERSION,
        )

    @classmethod
    def load(cls, path: str, *, strict: bool = True) -> "EthosFile":
        """Read and parse a `.ethos` file from disk."""
        with open(path, "r", encoding="utf-8") as fh:
            try:
                raw = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValidationError([f"file is not valid JSON: {exc}"]) from exc
        return cls.from_dict(raw, strict=strict)

    # -- serialisation ------------------------------------------------------

    def to_dict(self, *, include_integrity: bool = True) -> dict[str, Any]:
        """Serialise to a plain dict.

        `include_integrity=False` produces the pre-digest form used by §2 step 2.
        """
        out: dict[str, Any] = {
            "ethos_version": self.ethos_version,
            "subject": self.subject.to_dict(),
            "values": [v.to_dict() for v in self.values],
            "directives": self.directives.to_dict(),
        }
        if include_integrity:
            out["integrity"] = self.integrity.to_dict()
        return out

    def save(self, path: str) -> str:
        """Recompute the digest, then write the file. Returns the digest."""
        self.integrity.algorithm = DIGEST_ALGORITHM
        self.integrity.digest = compute_digest(self)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return self.integrity.digest

    # -- queries ------------------------------------------------------------

    def values_by_firmness(self, firmness: Firmness) -> list[Value]:
        """Values of one firmness level, preserving declaration order."""
        return [v for v in self.values if v.firmness is firmness]

    def axes(self) -> list[str]:
        """Distinct axes present, in first-appearance order."""
        seen: list[str] = []
        for v in self.values:
            if v.axis and v.axis not in seen:
                seen.append(v.axis)
        return seen

    def get(self, value_id: str) -> Optional[Value]:
        for v in self.values:
            if v.id == value_id:
                return v
        return None

    def summary(self) -> str:
        counts = {f: len(self.values_by_firmness(f)) for f in Firmness}
        return (
            f"{self.subject.label} · rev {self.subject.revision} · "
            f"{len(self.values)} values "
            f"({counts[Firmness.ABSOLUTE]} absolute, "
            f"{counts[Firmness.STRONG]} strong, "
            f"{counts[Firmness.PREFERENCE]} preference)"
        )


# ---------------------------------------------------------------------------
# Canonical digest — SPEC.md §2
# ---------------------------------------------------------------------------


def canonical_bytes(source: EthosFile | dict[str, Any]) -> bytes:
    """Canonical byte serialisation per SPEC.md §2 steps 1–4.

    Keys sorted at every depth, no whitespace in separators, `integrity`
    removed, array order preserved, UTF-8, no trailing newline.

    Array order matters: `values` encodes conflict priority, so sorting it
    would silently change meaning.
    """
    if isinstance(source, EthosFile):
        payload = source.to_dict(include_integrity=False)
    else:
        payload = {k: v for k, v in source.items() if k != "integrity"}

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_digest(source: EthosFile | dict[str, Any]) -> str:
    """Lowercase hex SHA-256 of the canonical bytes (SPEC.md §2 step 5)."""
    return hashlib.sha256(canonical_bytes(source)).hexdigest()
