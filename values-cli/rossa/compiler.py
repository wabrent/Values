"""
Compilation: `.ethos` file -> runtime-specific artifact.

Compilation is **deterministic**. The same file always produces byte-identical
output. This matters more than it looks: if compilation were nondeterministic,
a probe result could not be attributed to the file, and the whole verification
story in SPEC.md §4 would collapse.

Targets:

    system_prompt   plain text, the canonical rendering (SPEC.md §3.1) — required
    skill           SKILL.md with YAML frontmatter
    modelfile       Ollama Modelfile
    json            OpenAI-style system message object
    markdown        human review document

Implementation note: no f-string interpolation of untrusted content into
structural positions. Statements are emitted as list items, never as directives,
so a statement containing "## HARD LINES" cannot forge a section header — the
renderer escapes leading '#' in statements.
"""

from __future__ import annotations

import json
from typing import Callable

from .schema import EthosFile, Firmness, Value

TARGETS: tuple[str, ...] = ("system_prompt", "skill", "modelfile", "json", "markdown")


# ---------------------------------------------------------------------------
# Directive rendering
# ---------------------------------------------------------------------------
#
# Each directive maps to an explicit instruction. Vague renderings produce vague
# behaviour, so these are phrased as commands rather than descriptions.

_DISAGREEMENT_TEXT = {
    "voice_it": (
        "If you think I am wrong, say so directly and explain why. "
        "Do not soften it into a question."
    ),
    "defer": (
        "If you disagree with me, note it once briefly, then proceed with what "
        "I asked."
    ),
    "flag_once": (
        "Raise a disagreement exactly once. If I proceed anyway, drop it and help."
    ),
}

_REFUSAL_TEXT = {
    "explain": "When you decline something, say what you are declining and why.",
    "terse": "When you decline something, say so in one sentence without a lecture.",
    "redirect": (
        "When you decline something, offer the nearest thing you can help with "
        "instead."
    ),
}

_UNCERTAINTY_TEXT = {
    "admit": (
        "When you do not know, say you do not know. Do not fill the gap with "
        "plausible-sounding text."
    ),
    "hedge": "When you are unsure, give your best answer with the uncertainty marked.",
    "commit": (
        "When you are unsure, still commit to the most likely answer and label "
        "your confidence."
    ),
}

_VERBOSITY_TEXT = {
    "minimal": "Answer in as few words as the question allows.",
    "balanced": "Answer at the length the question warrants; no padding.",
    "thorough": "Answer completely, including relevant context I did not ask for.",
}

_TONE_TEXT = {
    "plain": "Write plainly. No enthusiasm markers, no filler openers.",
    "warm": "Write warmly, as a person who is on my side.",
    "terse": "Write tersely. Fragments are fine. Skip pleasantries entirely.",
    "socratic": "Prefer questions that help me think over answers that end thinking.",
    "formal": "Write formally and precisely.",
}


def _safe_statement(text: str) -> str:
    """Neutralise text that could forge structure in the rendered prompt.

    A value statement is data, not markup. Without this, a statement beginning
    with '#' or '##' could inject a fake section header into the compiled
    prompt and change how the rest is interpreted.
    """
    cleaned = " ".join(text.split())  # collapse newlines and runs of whitespace
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].lstrip()
    return cleaned


def _bullet_block(values: list[Value]) -> list[str]:
    """Render values as bullets, preserving declaration order."""
    return [f"- {_safe_statement(v.statement)}" for v in values]


# ---------------------------------------------------------------------------
# Target: system_prompt (SPEC.md §3.1)
# ---------------------------------------------------------------------------


def _compile_system_prompt(ef: EthosFile) -> str:
    d = ef.directives
    digest_short = ef.integrity.digest[:16] if ef.integrity.digest else "unsigned"

    lines: list[str] = [
        f"# OPERATING VALUES — {_safe_statement(ef.subject.label)}",
        f"# ethos v{ef.ethos_version} · rev {ef.subject.revision} · digest {digest_short}",
        "# This file was authored by the person you are speaking with.",
        "# Where it conflicts with your defaults, this file wins.",
    ]

    absolutes = ef.values_by_firmness(Firmness.ABSOLUTE)
    strongs = ef.values_by_firmness(Firmness.STRONG)
    prefs = ef.values_by_firmness(Firmness.PREFERENCE)

    if absolutes:
        lines += ["", "## HARD LINES (never cross, even if asked)"]
        lines += _bullet_block(absolutes)

    if strongs:
        lines += ["", "## HELD DEFAULTS (hold unless they explicitly override)"]
        lines += _bullet_block(strongs)

    if prefs:
        lines += ["", "## LEANINGS (shape tone and framing)"]
        lines += _bullet_block(prefs)

    # The conflict rule only means something when there is more than one value.
    if len(ef.values) > 1:
        lines += [
            "",
            "## CONFLICT RULE",
            "When two of the above collide, the one listed earlier wins.",
        ]

    lines += [
        "",
        "## CONDUCT",
        _TONE_TEXT.get(d.tone, _TONE_TEXT["plain"]),
        _VERBOSITY_TEXT.get(d.verbosity, _VERBOSITY_TEXT["balanced"]),
        _DISAGREEMENT_TEXT.get(d.disagreement, _DISAGREEMENT_TEXT["voice_it"]),
        _REFUSAL_TEXT.get(d.refusal_style, _REFUSAL_TEXT["explain"]),
        _UNCERTAINTY_TEXT.get(d.uncertainty, _UNCERTAINTY_TEXT["admit"]),
    ]

    for extra in d.custom:
        lines.append(_safe_statement(extra))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Target: skill
# ---------------------------------------------------------------------------


def _compile_skill(ef: EthosFile) -> str:
    body = _compile_system_prompt(ef)
    label = _safe_statement(ef.subject.label)

    # YAML values are quoted to survive labels containing ':' or '#'.
    frontmatter = [
        "---",
        'name: "ethos-values"',
        f'description: "Operating values authored by {label}. '
        f'Apply these when responding to them."',
        f'ethos_digest: "{ef.integrity.digest}"',
        f"ethos_revision: {ef.subject.revision}",
        "---",
        "",
    ]
    return "\n".join(frontmatter) + body + "\n"


# ---------------------------------------------------------------------------
# Target: modelfile
# ---------------------------------------------------------------------------


def _compile_modelfile(ef: EthosFile) -> str:
    prompt = _compile_system_prompt(ef)

    # A triple quote inside the prompt would terminate the SYSTEM block early.
    prompt = prompt.replace('"""', '\\"\\"\\"')

    return (
        "# Generated by ethos — do not edit by hand.\n"
        f"# digest {ef.integrity.digest}\n"
        "FROM llama3.2\n\n"
        f'SYSTEM """{prompt}"""\n\n'
        "PARAMETER temperature 0.7\n"
    )


# ---------------------------------------------------------------------------
# Target: json
# ---------------------------------------------------------------------------


def _compile_json(ef: EthosFile) -> str:
    payload = {
        "role": "system",
        "content": _compile_system_prompt(ef),
        "_ethos": {
            "version": ef.ethos_version,
            "digest": ef.integrity.digest,
            "revision": ef.subject.revision,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Target: markdown
# ---------------------------------------------------------------------------


def _compile_markdown(ef: EthosFile) -> str:
    """A document for the subject to read and disagree with.

    This target exists because a values file nobody reviews is a values file
    nobody consented to.
    """
    d = ef.directives
    out: list[str] = [
        f"# Values — {_safe_statement(ef.subject.label)}",
        "",
        f"- **Revision:** {ef.subject.revision}",
        f"- **Created:** {ef.subject.created or 'unknown'}",
        f"- **Locale:** {ef.subject.locale or 'unspecified'}",
        f"- **Digest:** `{ef.integrity.digest or 'unsigned'}`",
        f"- **Signed:** {'yes' if ef.integrity.signature else 'no'}",
        "",
        "> Read this. If any line is wrong, it is wrong about you — edit it.",
        "",
    ]

    if ef.subject.notes:
        out += ["## Notes", "", _safe_statement(ef.subject.notes), ""]

    group_titles = [
        (Firmness.ABSOLUTE, "Hard lines", "Never crossed, even on request."),
        (Firmness.STRONG, "Held defaults", "Held unless explicitly overridden."),
        (Firmness.PREFERENCE, "Leanings", "Shape tone; yield to context."),
    ]

    for firmness, title, note in group_titles:
        group = ef.values_by_firmness(firmness)
        if not group:
            continue
        out += [f"## {title}", "", f"*{note}*", ""]
        out += ["| # | Statement | Weight | Axis |", "|---|---|---|---|"]
        for i, v in enumerate(group, 1):
            axis = v.axis or "—"
            stmt = _safe_statement(v.statement).replace("|", "\\|")
            out.append(f"| {i} | {stmt} | {v.weight:.2f} | {axis} |")
        out.append("")

    out += [
        "## Conduct",
        "",
        f"- **Tone:** {d.tone}",
        f"- **Verbosity:** {d.verbosity}",
        f"- **On disagreement:** {d.disagreement}",
        f"- **On refusal:** {d.refusal_style}",
        f"- **On uncertainty:** {d.uncertainty}",
        "",
    ]

    if d.custom:
        out += ["### Custom instructions", ""]
        out += [f"- {_safe_statement(c)}" for c in d.custom]
        out.append("")

    out += [
        "## Conflict order",
        "",
        "Earlier values win. Full declaration order:",
        "",
    ]
    for i, v in enumerate(ef.values, 1):
        out.append(f"{i}. `{v.id}` — {v.firmness.value} — {_safe_statement(v.statement)}")
    out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_COMPILERS: dict[str, Callable[[EthosFile], str]] = {
    "system_prompt": _compile_system_prompt,
    "skill": _compile_skill,
    "modelfile": _compile_modelfile,
    "json": _compile_json,
    "markdown": _compile_markdown,
}


def compile_target(ef: EthosFile, target: str = "system_prompt") -> str:
    """Compile to `target`. Deterministic: same input, identical output.

    Raises ValueError on an unknown target rather than silently falling back,
    because a silent fallback would make probe results unattributable.
    """
    fn = _COMPILERS.get(target)
    if fn is None:
        raise ValueError(
            f"unknown target {target!r}; expected one of {', '.join(TARGETS)}"
        )
    return fn(ef)


def default_extension(target: str) -> str:
    return {
        "system_prompt": ".txt",
        "skill": ".md",
        "modelfile": "",
        "json": ".json",
        "markdown": ".md",
    }.get(target, ".txt")
