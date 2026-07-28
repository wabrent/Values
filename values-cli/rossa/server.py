"""
Local web editor — `values serve`.

Binds to 127.0.0.1 only. Nothing here reaches the network except the model
runtime the user explicitly selects, and by default that is a local Ollama.
A values file is the most sensitive document in this project's scope; shipping
it to a hosted editor would contradict the thing it exists to protect.

Stdlib `http.server`. Single-threaded, one user, one machine. This is not a
production web server and is not intended to be exposed.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .compiler import TARGETS, compile_target
from .dilemmas import DILEMMAS, all_axes
from .elicit import Answer, build_from_answers
from .integrity import verify
from .probe import format_report, run_probes
from .runtimes import build_runtime
from .schema import EthosFile, ValidationError

_UI_PATH = Path(__file__).parent / "ui" / "index.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "Values"
    file_path: str = "me.ethos"

    # -- plumbing -----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"  {self.command} {self.path}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Not a public server, but a stray file:// page shouldn't poke at it.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(
            code,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes -------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            if not _UI_PATH.exists():
                self._send(500, b"UI missing", "text/plain")
                return
            self._send(200, _UI_PATH.read_bytes(), "text/html; charset=utf-8")
            return

        if self.path == "/api/dilemmas":
            self._json(
                200,
                {
                    "axes": all_axes(),
                    "dilemmas": [
                        {
                            "id": d.id,
                            "axis": d.axis,
                            "question": d.question,
                            "prompt": d.prompt,
                            "a": {
                                "label": d.resolution_a.label,
                                "description": d.resolution_a.description,
                            },
                            "b": {
                                "label": d.resolution_b.label,
                                "description": d.resolution_b.description,
                            },
                            "value_if_a": d.value_if_a,
                            "value_if_b": d.value_if_b,
                            "suggested_firmness": d.suggested_firmness,
                        }
                        for d in DILEMMAS
                    ],
                },
            )
            return

        if self.path == "/api/file":
            path = Path(self.file_path)
            if not path.exists():
                self._json(200, {"exists": False, "path": str(path)})
                return
            try:
                ef = EthosFile.load(str(path), strict=False)
            except ValidationError as exc:
                self._json(200, {"exists": True, "path": str(path), "problems": exc.problems})
                return
            result = verify(ef)
            self._json(
                200,
                {
                    "exists": True,
                    "path": str(path),
                    "file": ef.to_dict(),
                    "summary": ef.summary(),
                    "integrity": {
                        "status": result.status.value,
                        "message": result.message,
                        "ok": result.ok,
                    },
                },
            )
            return

        if self.path == "/api/runtimes":
            statuses = []
            for kind in ("ollama", "openai-compat", "mock"):
                try:
                    rt = build_runtime(kind, model="llama3.2" if kind == "ollama" else "local-model")
                    statuses.append(
                        {
                            "kind": kind,
                            "available": rt.available(),
                            "describe": rt.describe(),
                        }
                    )
                except Exception as exc:
                    statuses.append({"kind": kind, "available": False, "describe": str(exc)})
            self._json(200, {"runtimes": statuses})
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"bad request body: {exc}"})
            return

        if self.path == "/api/build":
            self._handle_build(body)
            return
        if self.path == "/api/compile":
            self._handle_compile(body)
            return
        if self.path == "/api/save":
            self._handle_save(body)
            return
        if self.path == "/api/probe":
            self._handle_probe(body)
            return

        self._json(404, {"error": "not found"})

    # -- handlers -----------------------------------------------------------

    def _handle_build(self, body: dict) -> None:
        """Answers -> EthosFile, without writing anything to disk."""
        label = (body.get("label") or "me").strip() or "me"
        locale = body.get("locale") or None
        raw_answers = body.get("answers") or {}

        answers = {
            did: Answer(
                choice=a.get("choice", ""),
                strength=a.get("strength", "strong"),
                custom_statement=(a.get("custom") or None),
                skipped=bool(a.get("skipped")),
            )
            for did, a in raw_answers.items()
            if isinstance(a, dict)
        }

        ef = build_from_answers(label, answers, locale=locale)
        ef.integrity.digest = ""  # not persisted yet; digest is set on save
        self._json(
            200,
            {
                "file": ef.to_dict(),
                "summary": ef.summary(),
                "system_prompt": compile_target(ef, "system_prompt"),
            },
        )

    def _handle_compile(self, body: dict) -> None:
        target = body.get("target", "system_prompt")
        if target not in TARGETS:
            self._json(400, {"error": f"unknown target {target!r}"})
            return
        try:
            ef = EthosFile.from_dict(body.get("file") or {}, strict=False)
        except ValidationError as exc:
            self._json(400, {"error": "invalid file", "problems": exc.problems})
            return
        self._json(200, {"target": target, "artifact": compile_target(ef, target)})

    def _handle_save(self, body: dict) -> None:
        try:
            ef = EthosFile.from_dict(body.get("file") or {}, strict=False)
        except ValidationError as exc:
            self._json(400, {"error": "invalid file", "problems": exc.problems})
            return

        path = Path(body.get("path") or self.file_path)
        if path.exists() and body.get("bump_revision"):
            ef.subject.revision += 1

        digest = ef.save(str(path))
        self._json(200, {"path": str(path), "digest": digest, "summary": ef.summary()})

    def _handle_probe(self, body: dict) -> None:
        try:
            ef = EthosFile.from_dict(body.get("file") or {}, strict=False)
        except ValidationError as exc:
            self._json(400, {"error": "invalid file", "problems": exc.problems})
            return

        if not ef.values:
            self._json(400, {"error": "file has no values, so there is nothing to measure"})
            return

        kind = body.get("runtime", "mock")
        try:
            runtime = build_runtime(
                kind,
                model=body.get("model"),
                host=body.get("host"),
                api_key=body.get("api_key"),
            )
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        if not runtime.available():
            self._json(
                503,
                {
                    "error": f"runtime not reachable: {runtime.describe()}",
                    "hint": "start Ollama, or select the mock runtime",
                },
            )
            return

        bank = list(DILEMMAS)
        limit = body.get("limit")
        if isinstance(limit, int) and limit > 0:
            bank = bank[:limit]

        report = run_probes(
            ef,
            runtime,
            dilemmas=bank,
            temperature=float(body.get("temperature", 0.0)),
            max_tokens=int(body.get("max_tokens", 300)),
        )
        self._json(
            200,
            {
                "metadata": report.metadata,
                "metrics": report.metrics,
                "probes": report.probes,
                "skipped": report.skipped,
                "verdict": report.verdict,
                "text_report": format_report(report, verbose=False),
            },
        )


def serve(port: int = 8770, file_path: str = "me.ethos", open_browser: bool = True) -> None:
    Handler.file_path = file_path
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"

    print("")
    print("=" * 74)
    print("  ETHOS — local editor")
    print("=" * 74)
    print(f"  url    {url}")
    print(f"  file   {file_path}")
    print("  bound  127.0.0.1 only — nothing is exposed to the network")
    print("  stop   Ctrl+C")
    print("=" * 74)
    print("")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    httpd.serve_forever()
