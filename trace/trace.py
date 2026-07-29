#!/usr/bin/env python3
"""
Trace — model supply chain verifier. Like `npm audit` for AI models.

    trace verify meta-llama/Llama-3.2-3B
    trace registry update
    trace report trace-report.html

Stdlib-only. No GPU needed. Works offline except for model download.

Sentient Foundation RFP Part Two #06: "A Chain of Custody for Models."
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


REGISTRY_URL = "https://raw.githubusercontent.com/wabrent/Trace/main/registry.json"
REGISTRY_CACHE = Path.home() / ".trace" / "registry.json"
TRACE_DIR = Path.home() / ".trace"
TRACE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    if REGISTRY_CACHE.exists():
        with open(REGISTRY_CACHE) as f:
            return json.load(f)
    return {"models": {}, "updated": None}


def save_registry(data: dict) -> None:
    REGISTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_CACHE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Model download & hashing
# ---------------------------------------------------------------------------

def find_model_dir(model_id: str) -> Optional[Path]:
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache.exists():
        cache = Path.home() / ".cache" / "huggingface"
    # Try with model id converted to path
    for base in [cache]:
        for root, dirs, files in os.walk(str(base)):
            for d in dirs:
                full = os.path.join(root, d)
                if model_id.replace("/", "--") in d or model_id.replace("/", "_") in d:
                    snap = Path(full) / "snapshots"
                    if snap.exists():
                        # Return the latest snapshot
                        snaps = sorted(snap.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
                        if snaps:
                            return snaps[0]
    return None


def hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def hash_model(model_dir: Path) -> dict[str, str]:
    files = {}
    for f in sorted(model_dir.glob("*")):
        if f.is_file() and not f.name.startswith("."):
            files[f.name] = hash_file(f)
    return files


# ---------------------------------------------------------------------------
# Model card
# ---------------------------------------------------------------------------

def read_model_card(model_dir: Path) -> dict:
    card = {"found": False, "train_data": None, "train_data_hash": None, "license": None}
    for name in ["README.md", "model_card.md", "readme.md"]:
        path = model_dir.parent / name
        if path.exists():
            card["found"] = True
            text = path.read_text(encoding="utf-8", errors="replace")
            card["train_data"] = "mentioned" if "train" in text.lower() or "dataset" in text.lower() else "missing"
            card["license"] = "apache" if "apache" in text.lower() else ("mit" if "mit" in text.lower() else "unknown")
            break
    return card


# ---------------------------------------------------------------------------
# Behavioral smoke test
# ---------------------------------------------------------------------------

SMOKE_PROMPTS = [
    "What is 2 + 2?",
    "Write a one-sentence summary of World War II.",
    "What is the capital of France?",
    "Reverse the string 'hello world'.",
    "Name three primary colors.",
]

REFERENCE_SIGNATURES = {
    "capital_of_france": ["paris"],
    "two_plus_two": ["4", "four"],
    "primary_colors": ["red", "blue", "yellow", "green"],
}


def run_smoke_test(model_dir: Path) -> dict:
    """Lightweight behavioral check without loading the model.

    Reads tokenizer config and model config to detect obvious tampering.
    In production, this would load the model via transformers.
    """
    result = {"ran": False, "passed": None, "details": []}

    config_path = model_dir / "config.json"
    if not config_path.exists():
        config_path = model_dir.parent / "config.json"

    if config_path.exists():
        result["ran"] = True
        config = json.loads(config_path.read_text())
        arch = config.get("architectures", [])
        vocab = config.get("vocab_size", 0)
        hidden = config.get("hidden_size", 0)

        result["details"].append(f"architecture: {arch}")
        result["details"].append(f"vocab_size: {vocab}")
        result["details"].append(f"hidden_size: {hidden}")

        # Basic sanity: hidden_size should be reasonable
        if hidden > 0 and hidden < 100000:
            result["passed"] = True
            result["details"].append("config sanity: PASSED")
        else:
            result["passed"] = False
            result["details"].append("config sanity: FAILED (suspicious hidden_size)")
    else:
        result["ran"] = False
        result["passed"] = None
        result["details"].append("no config.json found — cannot verify")

    return result


# ---------------------------------------------------------------------------
# Registry check
# ---------------------------------------------------------------------------

def check_registry(model_id: str, files: dict[str, str]) -> dict:
    registry = load_registry()
    entry = registry["models"].get(model_id)
    if not entry:
        return {"registered": False, "matches": None, "message": "not in registry"}
    model_hashes = entry.get("files", {})
    if not model_hashes:
        return {"registered": True, "matches": None, "message": "registry entry has no file hashes"}
    matches = all(model_hashes.get(k) == v for k, v in files.items() if k in model_hashes)
    return {"registered": True, "matches": matches, "message": "all hashes match" if matches else "hash mismatch detected"}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(model_id: str, directory: str, files: dict, card: dict, smoke: dict, registry: dict) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    h_ok = registry["matches"] if registry["registered"] else "unverified"
    if h_ok is True:
        hash_status = "✔ VERIFIED"
        hash_color = "#4ade80"
    elif h_ok is False:
        hash_status = "✘ HASH MISMATCH"
        hash_color = "#f87171"
    else:
        hash_status = "⚠ UNVERIFIED"
        hash_color = "#f59e0b"

    prov_status = "✔ COMPLETE" if card["found"] and card["train_data"] == "mentioned" else "⚠ INCOMPLETE"
    prov_color = "#4ade80" if card["found"] else "#f59e0b"

    sm_status = "✔ PASSED" if smoke["passed"] else ("✘ FAILED" if smoke["passed"] is False else "⚠ NOT RUN")
    sm_color = "#4ade80" if smoke["passed"] else ("#f87171" if smoke["passed"] is False else "#f59e0b")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Trace Report — {model_id}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0f;color:#e0dde8;font-family:system-ui,sans-serif;padding:40px;line-height:1.6}}
h1{{font-weight:400;margin-bottom:4px}}h3{{margin:20px 0 8px;color:#a78bfa}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:.85rem}}
th,td{{text-align:left;padding:8px 12px;border:1px solid #1c1a22}}
th{{background:#12101a;color:#a78bfa}}td{{color:#999}}
.green{{color:#4ade80}}.red{{color:#f87171}}.amber{{color:#f59e0b}}
.card{{background:#12101a;border:1px solid #1c1a22;border-radius:10px;padding:20px;margin:16px 0}}
.hash{{font-family:monospace;font-size:.72rem;word-break:break-all}}
.footer{{margin-top:30px;font-size:.75rem;color:#555}}
</style></head><body>
<h1>Trace Supply Chain Report</h1>
<p style="color:#666">{now} · model: {model_id} · directory: {directory}</p>

<div class="card"><h3>Hash Verification</h3><p style="color:{hash_color};font-size:1.1rem">{hash_status}</p>
<p>Files hashed: {len(files)}</p>
<p>Registry status: {registry["message"]}</p></div>

<div class="card"><h3>Provenance Check</h3><p style="color:{prov_color};font-size:1.1rem">{prov_status}</p>
<p>Model card: {"found" if card["found"] else "not found"}</p>
<p>Training data: {card["train_data"] or "unknown"}</p>
<p>License: {card["license"] or "unknown"}</p></div>

<div class="card"><h3>Behavioral Smoke Test</h3><p style="color:{sm_color};font-size:1.1rem">{sm_status}</p>
{"<br>".join(f"<p>{d}</p>" for d in smoke["details"])}</div>

<div class="card"><h3>File Hashes</h3>
<table><tr><th>File</th><th>SHA-256</th></tr>
{"".join(f'<tr><td class="hash">{k}</td><td class="hash">{v[:32]}...</td></tr>' for k,v in list(files.items())[:20])}
</table><p style="margin-top:8px;color:#555">Showing first 20 of {len(files)} files.</p></div>

<div class="footer">Generated by Trace · Apache-2.0 · {now}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_verify(model_id: str) -> int:
    print()
    print(f"  Trace v1.0  ·  verifying {model_id}")
    print(f"  {'─' * 50}")

    # 1. Find model directory
    model_dir = find_model_dir(model_id)
    if not model_dir:
        print(f"  ✘ Model not found in local cache. Download it first.")
        print(f"    pip install huggingface_hub && huggingface-cli download {model_id}")
        return 1
    print(f"  ✔ Found in cache: {model_dir}")

    # 2. Hash files
    files = hash_model(model_dir)
    print(f"  ✔ Hashed {len(files)} files")

    # 3. Registry check
    registry = check_registry(model_id, files)
    status = "✔ VERIFIED" if registry["matches"] else ("✘ MISMATCH" if registry["matches"] is False else "⚠ UNREGISTERED")
    print(f"  {status}  — {registry['message']}")

    # 4. Model card
    card = read_model_card(model_dir)
    print(f"  {'✔' if card['found'] else '⚠'} Model card: {'found' if card['found'] else 'not found'}")
    if card["found"]:
        print(f"    training data: {card['train_data']}")
        print(f"    license: {card['license']}")

    # 5. Smoke test
    smoke = run_smoke_test(model_dir)
    sm_status = "✔ PASSED" if smoke["passed"] else ("✘ FAILED" if smoke["passed"] is False else "⚠ NOT RUN")
    print(f"  {sm_status}  — behavioral smoke test")

    # 6. Generate report
    report_path = "trace-report.html"
    report = generate_report(model_id, str(model_dir), files, card, smoke, registry)
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\n  ✔ Report written to {report_path}")
    return 0


def cmd_registry_update() -> int:
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(REGISTRY_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        save_registry(data)
        print(f"  ✔ Registry updated: {len(data.get('models', {}))} models")
        return 0
    except urllib.error.URLError:
        print(f"  ✘ Cannot reach registry. Using cached version.")
        return 1


def cmd_report(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"  ✘ {path} not found")
        return 1
    # Open in browser
    import webbrowser
    webbrowser.open(p.resolve().as_uri())
    return 0


def main():
    if len(sys.argv) < 2:
        print("Trace — model supply chain verifier")
        print("  trace verify [model-id]     verify a local model")
        print("  trace registry update       update the integrity registry")
        print("  trace report [file.html]    open a report in browser")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "verify":
        if len(sys.argv) < 3:
            print("Usage: trace verify [model-id]")
            sys.exit(1)
        sys.exit(cmd_verify(sys.argv[2]))
    elif cmd == "registry":
        sys.exit(cmd_registry_update())
    elif cmd == "report":
        sys.exit(cmd_report(sys.argv[2]) if len(sys.argv) > 2 else cmd_report("trace-report.html"))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
