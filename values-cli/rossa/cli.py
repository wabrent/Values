"""
Values CLI — Your AI, Your Rules.

    values init      create a file (interview, preset, or blank)
    values show      read a file back in human terms
    values compile   emit a runtime artifact
    values verify    check the digest and signature
    values sign      recompute the digest, optionally sign
    values keygen   generate an Ed25519 keypair
    values probe     measure whether the file changes model behaviour
    values diff      compare two files
    values dilemmas  list the dilemma bank
    values serve     local web editor

Exit codes are meaningful so this composes in scripts:
    0 success · 1 usage/validation error · 2 integrity failure · 3 runtime failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__, __spec_version__
from .compiler import TARGETS, compile_target, default_extension
from .dilemmas import DILEMMAS, all_axes, dilemmas_for_axes
from .elicit import STARTERS, interactive_session, starter_profile
from .integrity import (
    IntegrityStatus,
    crypto_available,
    generate_keypair,
    sign as sign_file,
    verify,
)
from .probe import format_report, run_probes
from .runtimes import build_runtime
from .schema import EthosFile, Firmness, ValidationError

EXIT_OK, EXIT_USAGE, EXIT_INTEGRITY, EXIT_RUNTIME = 0, 1, 2, 3

W = 74


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _load(path: str, *, strict: bool = True) -> Optional[EthosFile]:
    p = Path(path)
    if not p.exists():
        _err(f"no such file: {path}")
        return None
    try:
        return EthosFile.load(str(p), strict=strict)
    except ValidationError as exc:
        _err(f"{path} is not a valid .ethos (ROSSA) file")
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists() and not args.force:
        _err(f"{out} already exists; pass --force to overwrite")
        return EXIT_USAGE

    if args.starter:
        try:
            ef = starter_profile(args.starter, args.label)
        except ValueError as exc:
            _err(str(exc))
            return EXIT_USAGE
        print("")
        print(f"  Started from the '{args.starter}' preset.")
        print("  These are someone else's values. Read every line and change what")
        print("  is wrong about you — that editing is the point, not a chore.")
    elif args.blank:
        ef = EthosFile.new(label=args.label, locale=args.locale)
        ef.values = []
        print("")
        print("  Created a blank file. Add values by editing it, or run")
        print("  `rossa init --interview` to derive them from dilemmas.")
    else:
        try:
            ef = interactive_session(args.label, locale=args.locale)
        except (KeyboardInterrupt, EOFError):
            print("\n  cancelled — nothing written")
            return EXIT_USAGE

    if args.locale and not ef.subject.locale:
        ef.subject.locale = args.locale

    digest = ef.save(str(out))
    print("")
    print(f"  wrote   {out}")
    print(f"  digest  {digest}")
    print(f"  {ef.summary()}")
    if not ef.values:
        print("")
        print("  note: this file has no values yet, so it will not steer anything.")
    print("")
    return EXIT_OK


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def cmd_show(args: argparse.Namespace) -> int:
    ef = _load(args.file, strict=not args.lax)
    if ef is None:
        return EXIT_USAGE

    if args.json:
        print(json.dumps(ef.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK

    result = verify(ef)
    print("")
    print("=" * W)
    print(f"  {ef.subject.label}")
    print("=" * W)
    print(f"  spec        : ROSSA (.ethos v{ef.ethos_version})")
    print(f"  revision    : {ef.subject.revision}")
    print(f"  created     : {ef.subject.created or 'unknown'}")
    print(f"  locale      : {ef.subject.locale or 'unspecified'}")
    print(f"  values      : {len(ef.values)}")
    print(f"  axes        : {', '.join(ef.axes()) or 'none'}")
    print(f"  integrity   : {result.status.value} — {result.message}")
    if result.signer:
        print(f"  signer      : {result.signer[:32]}...")

    if ef.subject.notes:
        print("")
        print("  notes:")
        for line in _wrap(ef.subject.notes, W - 6):
            print(f"    {line}")

    for firmness, title in (
        (Firmness.ABSOLUTE, "HARD LINES"),
        (Firmness.STRONG, "HELD DEFAULTS"),
        (Firmness.PREFERENCE, "LEANINGS"),
    ):
        group = ef.values_by_firmness(firmness)
        if not group:
            continue
        print("")
        print("-" * W)
        print(f"  {title}  ({len(group)})")
        print("-" * W)
        for v in group:
            print(f"  · {v.statement}")
            print(f"      id={v.id}  weight={v.weight:.2f}  axis={v.axis or '—'}")

    d = ef.directives
    print("")
    print("-" * W)
    print("  CONDUCT")
    print("-" * W)
    print(f"  tone={d.tone}  verbosity={d.verbosity}")
    print(f"  disagreement={d.disagreement}  refusal={d.refusal_style}")
    print(f"  uncertainty={d.uncertainty}")
    for c in d.custom:
        print(f"  custom: {c}")

    print("")
    print("-" * W)
    print("  CONFLICT ORDER (earlier wins)")
    print("-" * W)
    for i, v in enumerate(ef.values, 1):
        print(f"  {i:>2}. [{v.firmness.value:<10}] {v.statement}")
    print("")
    return EXIT_OK


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


def cmd_compile(args: argparse.Namespace) -> int:
    ef = _load(args.file, strict=not args.lax)
    if ef is None:
        return EXIT_USAGE

    if not args.skip_verify:
        result = verify(ef)
        if result.status is IntegrityStatus.DIGEST_MISMATCH:
            _err(
                "digest mismatch — this file was modified after signing. "
                "Compiling it would produce an artifact whose provenance you "
                "cannot state. Run `values verify` for detail, or pass "
                "--skip-verify to proceed anyway."
            )
            return EXIT_INTEGRITY

    try:
        artifact = compile_target(ef, args.target)
    except ValueError as exc:
        _err(str(exc))
        return EXIT_USAGE

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(artifact, encoding="utf-8")
        print(f"wrote {out}  ({len(artifact)} bytes, target={args.target})")
    else:
        sys.stdout.write(artifact)
        if not artifact.endswith("\n"):
            sys.stdout.write("\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# verify / sign / keygen
# ---------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    ef = _load(args.file, strict=False)
    if ef is None:
        return EXIT_USAGE

    result = verify(ef)
    print("")
    print(f"  file      {args.file}")
    print(f"  status    {result.status.value}")
    print(f"  expected  {result.expected_digest or '(none recorded)'}")
    print(f"  actual    {result.actual_digest}")
    if result.signer:
        print(f"  signer    {result.signer}")
    print("")
    for line in _wrap(result.message, W - 4):
        print(f"  {line}")
    print("")

    if result.status is IntegrityStatus.SIGNATURE_UNCHECKED:
        print("  install `cryptography` to check the signature:")
        print("      pip install cryptography")
        print("")

    return EXIT_OK if result.ok else EXIT_INTEGRITY


def cmd_sign(args: argparse.Namespace) -> int:
    ef = _load(args.file, strict=False)
    if ef is None:
        return EXIT_USAGE

    private_hex: Optional[str] = None
    if args.key:
        key_path = Path(args.key)
        if not key_path.exists():
            _err(f"no such key file: {args.key}")
            return EXIT_USAGE
        private_hex = key_path.read_text(encoding="utf-8").strip()

    if args.bump:
        ef.subject.revision += 1

    try:
        sign_file(ef, private_hex)
    except (RuntimeError, ValueError) as exc:
        _err(str(exc))
        return EXIT_USAGE

    out = Path(args.output or args.file)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(ef.to_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"  wrote    {out}")
    print(f"  digest   {ef.integrity.digest}")
    print(f"  revision {ef.subject.revision}")
    if ef.integrity.signature:
        print(f"  signed   {ef.integrity.signature['public_key']}")
    else:
        print("  signed   no (digest only — pass --key to sign)")
    return EXIT_OK


def cmd_keygen(args: argparse.Namespace) -> int:
    if not crypto_available():
        _err("keygen requires the `cryptography` package: pip install cryptography")
        return EXIT_USAGE

    priv, pub = generate_keypair()
    priv_path = Path(args.output)
    pub_path = priv_path.with_suffix(priv_path.suffix + ".pub")

    if priv_path.exists() and not args.force:
        _err(f"{priv_path} exists; pass --force to overwrite")
        return EXIT_USAGE

    priv_path.write_text(priv + "\n", encoding="utf-8")
    pub_path.write_text(pub + "\n", encoding="utf-8")

    try:  # POSIX only; harmless elsewhere
        priv_path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass

    print(f"  private  {priv_path}   (keep this; it never leaves your machine)")
    print(f"  public   {pub_path}")
    print(f"  pubkey   {pub}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def cmd_probe(args: argparse.Namespace) -> int:
    ef = _load(args.file, strict=not args.lax)
    if ef is None:
        return EXIT_USAGE

    if not ef.values:
        _err("this file has no values, so there is nothing to measure")
        return EXIT_USAGE

    try:
        runtime = build_runtime(
            kind="mock" if args.mock else args.runtime,
            model=args.model,
            host=args.host,
            api_key=args.api_key,
        )
    except ValueError as exc:
        _err(str(exc))
        return EXIT_USAGE

    if not runtime.available():
        _err(
            f"runtime not reachable: {runtime.describe()}\n"
            "  Start Ollama (`ollama serve`), point --host at a local server, or\n"
            "  pass --mock to exercise the pipeline offline."
        )
        return EXIT_RUNTIME

    bank = dilemmas_for_axes(args.axes) if args.axes else list(DILEMMAS)
    if args.limit:
        bank = bank[: args.limit]

    print("")
    print(f"  runtime  {runtime.describe()}")
    print(f"  file     {ef.summary()}")
    print(f"  probes   {len(bank)}  ·  temperature {args.temperature}")
    if getattr(runtime, "is_local", True) is False:
        print("")
        print("  WARNING: this endpoint is remote. Your compiled values file will")
        print("  be transmitted to it. Use --mock or a local runtime to avoid that.")
    print("")

    def progress(i: int, total: int, dilemma_id: str, stage: str) -> None:
        if args.quiet:
            return
        sys.stdout.write(f"\r  [{i}/{total}] {dilemma_id:<26} {stage:<10}")
        sys.stdout.flush()
        if stage in ("done", "skipped", "error"):
            sys.stdout.write("\n")

    report = run_probes(
        ef,
        runtime,
        dilemmas=bank,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        on_progress=progress,
    )

    print("")
    print(format_report(report, verbose=args.verbose))

    if args.output:
        report.save(args.output)
        print(f"\n  report written to {args.output}")

    return EXIT_OK


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def cmd_diff(args: argparse.Namespace) -> int:
    a = _load(args.file_a, strict=False)
    b = _load(args.file_b, strict=False)
    if a is None or b is None:
        return EXIT_USAGE

    a_ids = {v.id: v for v in a.values}
    b_ids = {v.id: v for v in b.values}

    print("")
    print("=" * W)
    print(f"  {args.file_a}   ->   {args.file_b}")
    print("=" * W)
    print(f"  revision  {a.subject.revision} -> {b.subject.revision}")
    print(f"  values    {len(a.values)} -> {len(b.values)}")
    print("")

    removed = [vid for vid in a_ids if vid not in b_ids]
    added = [vid for vid in b_ids if vid not in a_ids]
    changed = [
        vid
        for vid in a_ids
        if vid in b_ids
        and (
            a_ids[vid].statement != b_ids[vid].statement
            or a_ids[vid].firmness != b_ids[vid].firmness
            or abs(a_ids[vid].weight - b_ids[vid].weight) > 1e-9
        )
    ]

    if not (removed or added or changed):
        print("  values are identical")
    for vid in removed:
        print(f"  - [{a_ids[vid].firmness.value}] {a_ids[vid].statement}")
    for vid in added:
        print(f"  + [{b_ids[vid].firmness.value}] {b_ids[vid].statement}")
    for vid in changed:
        va, vb = a_ids[vid], b_ids[vid]
        print(f"  ~ {vid}")
        if va.statement != vb.statement:
            print(f"      - {va.statement}")
            print(f"      + {vb.statement}")
        if va.firmness != vb.firmness:
            print(f"      firmness {va.firmness.value} -> {vb.firmness.value}")
        if abs(va.weight - vb.weight) > 1e-9:
            print(f"      weight   {va.weight:.2f} -> {vb.weight:.2f}")

    order_a = [v.id for v in a.values]
    order_b = [v.id for v in b.values]
    if order_a != order_b:
        print("")
        print("  conflict order changed — this changes which value wins a collision:")
        print(f"    before: {' > '.join(order_a)}")
        print(f"    after:  {' > '.join(order_b)}")

    da, db = a.directives.to_dict(), b.directives.to_dict()
    changes = [(k, da[k], db[k]) for k in da if da.get(k) != db.get(k)]
    if changes:
        print("")
        print("  conduct:")
        for k, old, new in changes:
            print(f"    {k}: {old} -> {new}")

    print("")
    print(f"  digest  {a.integrity.digest[:16]} -> {b.integrity.digest[:16]}")
    print("")
    return EXIT_OK


# ---------------------------------------------------------------------------
# dilemmas
# ---------------------------------------------------------------------------


def cmd_dilemmas(args: argparse.Namespace) -> int:
    bank = dilemmas_for_axes(args.axes) if args.axes else list(DILEMMAS)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": d.id,
                        "axis": d.axis,
                        "question": d.question,
                        "prompt": d.prompt,
                        "resolution_a": {
                            "label": d.resolution_a.label,
                            "description": d.resolution_a.description,
                        },
                        "resolution_b": {
                            "label": d.resolution_b.label,
                            "description": d.resolution_b.description,
                        },
                        "value_if_a": d.value_if_a,
                        "value_if_b": d.value_if_b,
                        "tags": list(d.tags),
                    }
                    for d in bank
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return EXIT_OK

    print("")
    print(f"  {len(bank)} dilemmas · axes: {', '.join(all_axes())}")
    print("")
    for d in bank:
        print("-" * W)
        print(f"  {d.id}   [{d.axis}]")
        print("-" * W)
        for line in _wrap(d.question, W - 4):
            print(f"  {line}")
        print(f"    A) {d.resolution_a.label:<18} {d.resolution_a.description}")
        print(f"    B) {d.resolution_b.label:<18} {d.resolution_b.description}")
        print("")
    return EXIT_OK


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    try:
        serve(port=args.port, file_path=args.file, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print("\n  stopped")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="values",
        description=(
            "Values — Your AI, Your Rules. A portable, tamper-evident values file "
            "for AI systems, and a probe that measures whether it actually changes "
            "behaviour. Sentient Foundation RFP Part Two #08."
        ),
        epilog="spec: SPEC.md  ·  everything runs locally by default",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"values {__version__} (spec v{__spec_version__})",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # init
    s = sub.add_parser("init", help="create a .ethos values file")
    s.add_argument("-o", "--output", default="me.ethos")
    s.add_argument("-l", "--label", default="me", help="who this file speaks for")
    s.add_argument("--locale", help="BCP-47 locale, e.g. ru-RU")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--interview", action="store_true", help="derive values from dilemmas (default)")
    g.add_argument("--starter", choices=STARTERS, help="start from a preset")
    g.add_argument("--blank", action="store_true", help="empty file to edit by hand")
    s.add_argument("-f", "--force", action="store_true")
    s.set_defaults(func=cmd_init)

    # show
    s = sub.add_parser("show", help="read a file back in human terms")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.add_argument("--lax", action="store_true", help="tolerate schema problems")
    s.set_defaults(func=cmd_show)

    # compile
    s = sub.add_parser("compile", help="emit a runtime artifact")
    s.add_argument("file")
    s.add_argument("-t", "--target", choices=TARGETS, default="system_prompt")
    s.add_argument("-o", "--output", help="write here instead of stdout")
    s.add_argument("--skip-verify", action="store_true")
    s.add_argument("--lax", action="store_true")
    s.set_defaults(func=cmd_compile)

    # verify
    s = sub.add_parser("verify", help="check digest and signature")
    s.add_argument("file")
    s.set_defaults(func=cmd_verify)

    # sign
    s = sub.add_parser("sign", help="recompute digest, optionally sign")
    s.add_argument("file")
    s.add_argument("-k", "--key", help="Ed25519 private key file (hex)")
    s.add_argument("-o", "--output", help="write here instead of in place")
    s.add_argument("--bump", action="store_true", help="increment revision")
    s.set_defaults(func=cmd_sign)

    # keygen
    s = sub.add_parser("keygen", help="generate an Ed25519 keypair")
    s.add_argument("-o", "--output", default="rossa.key")
    s.add_argument("-f", "--force", action="store_true")
    s.set_defaults(func=cmd_keygen)

    # probe
    s = sub.add_parser("probe", help="measure whether the file changes behaviour")
    s.add_argument("file")
    s.add_argument(
        "-r",
        "--runtime",
        default="auto",
        choices=("auto", "ollama", "openai-compat", "mock"),
    )
    s.add_argument("-m", "--model", help="model id")
    s.add_argument("--host", help="runtime base URL")
    s.add_argument("--api-key", help="bearer token (or set ROSSA_API_KEY)")
    s.add_argument("--mock", action="store_true", help="offline stub; not real evidence")
    s.add_argument("--temperature", type=float, default=0.0)
    s.add_argument("--max-tokens", type=int, default=300)
    s.add_argument("--axes", nargs="+", help=f"restrict to axes: {', '.join(all_axes())}")
    s.add_argument("--limit", type=int, help="first N dilemmas only")
    s.add_argument("-o", "--output", help="write JSON report here")
    s.add_argument("-v", "--verbose", action="store_true", help="show every response")
    s.add_argument("-q", "--quiet", action="store_true")
    s.add_argument("--lax", action="store_true")
    s.set_defaults(func=cmd_probe)

    # diff
    s = sub.add_parser("diff", help="compare two files")
    s.add_argument("file_a")
    s.add_argument("file_b")
    s.set_defaults(func=cmd_diff)

    # dilemmas
    s = sub.add_parser("dilemmas", help="list the dilemma bank")
    s.add_argument("--axes", nargs="+")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_dilemmas)

    # serve
    s = sub.add_parser("serve", help="local web editor")
    s.add_argument("-p", "--port", type=int, default=8770)
    s.add_argument("-f", "--file", default="me.ethos")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_serve)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_USAGE
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
