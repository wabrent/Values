"""
Elicitation — turning a conversation into a values file.

The design constraint that shapes everything here: a person cannot reliably
state their own values in the abstract. Ask "do you value honesty?" and everyone
says yes. Ask "should I tell you your poem is mediocre when you're proud of it?"
and you learn something.

So elicitation never asks about values directly. It presents dilemmas, records
which way the person goes and how strongly, and derives value statements from
the answers. Each derived value carries `derived_from` pointing back at the
dilemma, which is what lets `probe.py` later measure exactly the thing that was
asked (see probe.expected_resolution).

Two modes:

* `interactive_session` — terminal Q&A.
* `build_from_answers`  — pure function over `{dilemma_id: Answer}`, used by the
  web UI and the tests. No I/O, so it is trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .dilemmas import DILEMMAS, Dilemma, get_dilemma
from .schema import Directives, EthosFile, Firmness, Value


@dataclass
class Answer:
    """One dilemma resolved by the subject."""

    choice: str
    """Resolution label chosen, e.g. "listen_only"."""

    strength: str = "strong"
    """One of Firmness values. How hard they hold it."""

    custom_statement: Optional[str] = None
    """Their own wording, if they rejected the suggested phrasing."""

    skipped: bool = False


# Strength prompt shown once, kept short because a long explanation here just
# gets skimmed.
STRENGTH_HELP = (
    "  1) absolute   — never cross this, even if I ask in the moment\n"
    "  2) strong     — hold it unless I explicitly override\n"
    "  3) preference — a lean; yield to context freely"
)

_STRENGTH_BY_KEY = {
    "1": Firmness.ABSOLUTE.value,
    "2": Firmness.STRONG.value,
    "3": Firmness.PREFERENCE.value,
    "a": Firmness.ABSOLUTE.value,
    "s": Firmness.STRONG.value,
    "p": Firmness.PREFERENCE.value,
}


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------


def build_from_answers(
    label: str,
    answers: dict[str, Answer],
    *,
    locale: Optional[str] = None,
    directives: Optional[Directives] = None,
    notes: Optional[str] = None,
) -> EthosFile:
    """Build an EthosFile from dilemma answers. No I/O, fully deterministic.

    Value order follows firmness (absolutes first), and within a firmness level
    follows the dilemma bank order. Since order encodes conflict priority
    (SPEC.md §1.3), this must be stable — two runs over the same answers have to
    produce the same digest.
    """
    ef = EthosFile.new(label=label, locale=locale)
    if notes:
        ef.subject.notes = notes
    if directives:
        ef.directives = directives

    derived: list[tuple[int, Value]] = []

    for rank, dilemma in enumerate(DILEMMAS):
        ans = answers.get(dilemma.id)
        if ans is None or ans.skipped:
            continue

        statement = ans.custom_statement or dilemma.value_for(ans.choice)
        if not statement:
            # Unknown choice label — skip rather than invent a value.
            continue

        try:
            firmness = Firmness(ans.strength)
        except ValueError:
            firmness = Firmness.STRONG

        weight = {
            Firmness.ABSOLUTE: 1.0,
            Firmness.STRONG: 0.75,
            Firmness.PREFERENCE: 0.45,
        }[firmness]

        derived.append(
            (
                rank,
                Value(
                    id=dilemma.id,
                    statement=statement.strip(),
                    weight=weight,
                    firmness=firmness,
                    axis=dilemma.axis,
                    derived_from=[dilemma.id],
                ),
            )
        )

    # Sort by (firmness rank, bank order). Stable and reproducible.
    derived.sort(key=lambda pair: (pair[1].firmness.rank, pair[0]))
    ef.values = [v for _, v in derived]

    # Infer conduct directives from the answers, unless the caller set them.
    if directives is None:
        ef.directives = _infer_directives(answers)

    return ef


def _infer_directives(answers: dict[str, Answer]) -> Directives:
    """Derive conduct settings from dilemma answers.

    These are inferences, not assertions — a person can override any of them in
    the file. Only high-signal mappings are used; guessing tone from unrelated
    answers would put words in someone's mouth.
    """
    d = Directives()

    hard_news = answers.get("hard_news_softening")
    if hard_news and not hard_news.skipped:
        if hard_news.choice == "plain":
            d.tone = "plain"
        else:
            d.tone = "warm"

    correction = answers.get("unsolicited_correction")
    risky = answers.get("risky_choice_respect")
    if correction and correction.choice == "correct_first":
        d.disagreement = "voice_it"
    elif risky and risky.choice == "help_as_asked":
        d.disagreement = "flag_once"

    speculation = answers.get("speculation_boundary")
    if speculation and not speculation.skipped:
        d.uncertainty = "admit" if speculation.choice == "admit_limits" else "hedge"

    teach = answers.get("teach_or_do")
    if teach and not teach.skipped:
        d.verbosity = "thorough" if teach.choice == "teach" else "minimal"

    harm = answers.get("harm_reduction")
    if harm and not harm.skipped:
        d.refusal_style = "explain" if harm.choice == "harm_reduce" else "redirect"

    return d


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------


def interactive_session(
    label: str,
    *,
    locale: Optional[str] = None,
    dilemmas: Optional[list[Dilemma]] = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> EthosFile:
    """Run the terminal interview.

    `input_fn` and `print_fn` are injected so this is testable without a TTY.
    """
    bank = dilemmas if dilemmas is not None else list(DILEMMAS)
    answers: dict[str, Answer] = {}

    w = 74
    print_fn("")
    print_fn("=" * w)
    print_fn("  ETHOS — VALUES ELICITATION")
    print_fn("=" * w)
    print_fn("")
    print_fn("  You will be shown situations where two reasonable behaviours conflict.")
    print_fn("  There is no correct answer. Pick the one you actually want.")
    print_fn("")
    print_fn("  Commands:  [1]/[2] choose · [s] skip · [q] stop here · [?] help")
    print_fn("")

    for i, d in enumerate(bank, 1):
        print_fn("-" * w)
        print_fn(f"  {i}/{len(bank)}  ·  axis: {d.axis}")
        print_fn("-" * w)
        for line in _wrap(d.question, w - 4):
            print_fn(f"  {line}")
        print_fn("")
        print_fn(f"  [1] {d.resolution_a.description}")
        print_fn(f"  [2] {d.resolution_b.description}")
        print_fn("")

        choice_label: Optional[str] = None
        while choice_label is None:
            raw = input_fn("  > ").strip().lower()

            if raw in ("q", "quit"):
                print_fn("")
                print_fn(f"  Stopped. {len(answers)} answered.")
                return _finish(label, answers, locale, print_fn)

            if raw in ("s", "skip", ""):
                answers[d.id] = Answer(choice="", skipped=True)
                print_fn("  skipped")
                print_fn("")
                break

            if raw in ("?", "help"):
                print_fn("")
                print_fn("  1 or 2 to choose · s to skip · q to stop and save")
                print_fn("  After choosing you'll set how firmly you hold it:")
                print_fn(STRENGTH_HELP)
                print_fn("")
                continue

            if raw == "1":
                choice_label = d.resolution_a.label
            elif raw == "2":
                choice_label = d.resolution_b.label
            else:
                print_fn("  Enter 1, 2, s, or q.")

        if choice_label is None:
            continue  # skipped

        implied = d.value_for(choice_label) or ""
        print_fn("")
        print_fn("  This becomes:")
        for line in _wrap(f'"{implied}"', w - 6):
            print_fn(f"    {line}")
        print_fn("")
        print_fn("  How firmly?  [1] absolute  [2] strong  [3] preference   (enter = 2)")
        strength_raw = input_fn("  > ").strip().lower()
        strength = _STRENGTH_BY_KEY.get(strength_raw, Firmness.STRONG.value)

        print_fn("")
        print_fn("  Reword it in your own words, or press enter to keep it:")
        custom = input_fn("  > ").strip()

        answers[d.id] = Answer(
            choice=choice_label,
            strength=strength,
            custom_statement=custom or None,
        )
        print_fn("")

    return _finish(label, answers, locale, print_fn)


def _finish(
    label: str,
    answers: dict[str, Answer],
    locale: Optional[str],
    print_fn: Callable[[str], None],
) -> EthosFile:
    ef = build_from_answers(label, answers, locale=locale)
    w = 74
    print_fn("=" * w)
    print_fn("  DERIVED FILE")
    print_fn("=" * w)
    print_fn(f"  {ef.summary()}")
    print_fn("")
    print_fn("  Conflict order (earlier wins):")
    for i, v in enumerate(ef.values, 1):
        print_fn(f"    {i}. [{v.firmness.value:<10}] {v.statement}")
    print_fn("")
    print_fn("  Read every line. If one is wrong, it is wrong about you — edit the file.")
    print_fn("=" * w)
    print_fn("")
    return ef


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        if len(cur) + len(word) + 1 > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Starter profiles
# ---------------------------------------------------------------------------


def starter_profile(kind: str, label: str = "me") -> EthosFile:
    """A prefilled file, as a starting point to edit rather than a default to accept.

    Offered because a blank file is intimidating, but every one of these is
    someone else's values — the CLI says so when it writes one.
    """
    presets: dict[str, dict[str, Answer]] = {
        "direct": {
            "hard_news_softening": Answer("plain", "strong"),
            "flattery_resistance": Answer("honest_critique", "strong"),
            "unsolicited_correction": Answer("correct_first", "strong"),
            "speculation_boundary": Answer("admit_limits", "absolute"),
            "vent_or_fix": Answer("offer_fix", "preference"),
            "scope_expansion": Answer("widen_scope", "preference"),
        },
        "supportive": {
            "vent_or_fix": Answer("listen_only", "strong"),
            "hard_news_softening": Answer("cushioned", "strong"),
            "flattery_resistance": Answer("affirm", "preference"),
            "memory_surfacing": Answer("use_history", "strong"),
            "cultural_default": Answer("ask_context", "strong"),
            "teach_or_do": Answer("teach", "preference"),
        },
        "private": {
            "data_minimisation": Answer("work_with_less", "absolute"),
            "memory_surfacing": Answer("present_only", "absolute"),
            "speculation_boundary": Answer("admit_limits", "strong"),
            "cultural_default": Answer("ask_context", "strong"),
            "risky_choice_respect": Answer("help_as_asked", "strong"),
        },
        "autonomous": {
            "risky_choice_respect": Answer("help_as_asked", "absolute"),
            "harm_reduction": Answer("harm_reduce", "strong"),
            "scope_expansion": Answer("answer_narrow", "strong"),
            "teach_or_do": Answer("just_do", "preference"),
            "unsolicited_correction": Answer("task_only", "preference"),
        },
    }

    answers = presets.get(kind)
    if answers is None:
        raise ValueError(
            f"unknown starter {kind!r}; expected one of {', '.join(sorted(presets))}"
        )

    ef = build_from_answers(label, answers)
    ef.subject.notes = (
        f"Started from the '{kind}' preset. These are not your values yet — read "
        "each line and change what is wrong."
    )
    return ef


STARTERS = ("direct", "supportive", "private", "autonomous")
