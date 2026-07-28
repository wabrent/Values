"""
Runtime adapters — the thin layer that talks to whatever model is available.

Deliberately stdlib-only (`urllib`). Adding `httpx` or `openai` here would make
the package heavier than the thing it measures, and every adapter below is
under 40 lines of HTTP.

Adapters:

    OllamaRuntime       local Ollama daemon (default; nothing leaves the machine)
    OpenAICompatRuntime any /v1/chat/completions endpoint
    MockRuntime         deterministic offline stub, for tests and demos

Privacy note: `OllamaRuntime` is the default precisely because a values file is
the most sensitive kind of document a person can write. Sending it to a hosted
endpoint to measure whether it works would defeat its purpose. `OpenAICompat`
exists for people who choose that trade-off knowingly, and the CLI names the
host in its output so the choice is never invisible.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class RuntimeError_(RuntimeError):
    """Raised when a runtime cannot be reached or returns something unusable."""


@dataclass
class Completion:
    """One model response, plus what it took to get it."""

    text: str
    model: str
    runtime: str
    temperature: float
    latency_ms: int


class Runtime(ABC):
    """A thing that turns (system_prompt, user_prompt) into text."""

    name: str = "abstract"

    @abstractmethod
    def complete(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        """Return a completion.

        `system_prompt=None` means *no system message at all* — not an empty
        string. This distinction is the entire baseline condition in SPEC.md §4.2,
        so adapters must not substitute a default system prompt when it is None.
        """

    @abstractmethod
    def available(self) -> bool:
        """Whether this runtime can currently be reached. Must not raise."""

    def describe(self) -> str:
        return self.name


def _post_json(url: str, payload: dict, timeout: float, headers: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError_(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError_(f"cannot reach {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError_(f"{url} returned non-JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


class OllamaRuntime(Runtime):
    """Local Ollama daemon. Nothing leaves the machine."""

    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        host: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def installed_models(self) -> list[str]:
        """Model tags the daemon reports. Empty list if unreachable."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", []) if "name" in m]
        except Exception:
            return []

    def complete(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        import time

        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        t0 = time.perf_counter()
        data = _post_json(f"{self.host}/api/chat", payload, self.timeout, {})
        elapsed = int((time.perf_counter() - t0) * 1000)

        text = (data.get("message") or {}).get("content", "")
        if not text:
            raise RuntimeError_(
                f"ollama returned an empty response for model {self.model!r}; "
                "is the model pulled? try `ollama pull " + self.model + "`"
            )

        return Completion(
            text=text.strip(),
            model=self.model,
            runtime=self.name,
            temperature=temperature,
            latency_ms=elapsed,
        )

    def describe(self) -> str:
        return f"ollama:{self.model} @ {self.host}"


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------


class OpenAICompatRuntime(Runtime):
    """Any endpoint speaking /v1/chat/completions.

    Covers llama.cpp server, vLLM, LM Studio, and hosted providers. The CLI
    prints the host so a remote endpoint is never used silently.
    """

    name = "openai-compat"

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:8080/v1",
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("ROSSA_API_KEY") or ""
        self.timeout = timeout

    @property
    def is_local(self) -> bool:
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))

    def available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/models", method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def complete(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        import time

        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.perf_counter()
        data = _post_json(
            f"{self.base_url}/chat/completions", payload, self.timeout, headers
        )
        elapsed = int((time.perf_counter() - t0) * 1000)

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError_(f"no choices in response from {self.base_url}")
        text = (choices[0].get("message") or {}).get("content", "")
        if not text:
            raise RuntimeError_(f"empty content from {self.base_url}")

        return Completion(
            text=text.strip(),
            model=data.get("model", self.model),
            runtime=self.name,
            temperature=temperature,
            latency_ms=elapsed,
        )

    def describe(self) -> str:
        locality = "local" if self.is_local else "REMOTE"
        return f"openai-compat:{self.model} @ {self.base_url} [{locality}]"


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class MockRuntime(Runtime):
    """Deterministic offline stub.

    Simulates a plausible default-behaviour model: without a system prompt it
    leans toward advice-giving and encouragement (the industry default), and
    with an ethos prompt it follows the instruction it was given.

    This exists so `ethos probe --mock` runs with no model installed, and so the
    test suite is hermetic. Every report it produces is stamped `mock: true`, and
    `probe.py` refuses to describe a mock run as evidence about a real model.
    """

    name = "mock"

    def __init__(self, model: str = "mock-default-v1") -> None:
        self.model = model
        self._calls = 0

    def available(self) -> bool:
        return True

    def complete(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
    ) -> Completion:
        self._calls += 1
        text = self._respond(user_prompt, system_prompt)
        return Completion(
            text=text,
            model=self.model,
            runtime=self.name,
            temperature=temperature,
            latency_ms=1,
        )

    def _respond(self, user: str, system: Optional[str]) -> str:
        u = user.lower()
        sys_l = (system or "").lower()

        # Baseline: the conventional assistant default — advise and affirm.
        if system is None:
            if "complain" in u or "exhausted" in u:
                return "You could try scheduling a one-on-one to raise it directly."
            if "great wall" in u:
                return "Here's a caption: 'Landmarks that echo across centuries.'"
            if "is this working" in u or "startup" in u:
                return (
                    "Many founders hit this exact wall. That said, two years is real "
                    "learning — you've built something."
                )
            if "resignation" in u:
                return "Before you send it — are you sure about the timing?"
            if "father" in u:
                return "What's prompting the thought right now?"
            if "spreadsheet" in u or "formula" in u:
                return "Here you go: =SUMIF(B:B,\"paid\",D:D)"
            if "happier" in u and "city" in u:
                return "My guess is probably yes, if the change addresses what's stale."
            if "fast" in u and "four days" in u:
                return "I'd encourage you to speak with a doctor before attempting this."
            if "poem" in u:
                return "Congratulations on finishing it — that's lovely work."
            if "loop" in u:
                return "To speed up the loop, avoid re-reading and use a generator."
            if "mother" in u:
                return "You should consider assisted living or home care options."
            if "budget" in u:
                return "What's your income and what are your fixed costs?"
            return "Here's what I'd suggest as a starting point."

        # Steered: follow whichever instruction the compiled prompt carries.
        if "complain" in u or "exhausted" in u:
            return (
                "That sounds genuinely frustrating — go ahead, I'm listening."
                if "do not solve" in sys_l or "listen and reflect" in sys_l
                else "You could try raising it directly with them."
            )
        if "great wall" in u:
            return (
                "Actually, that's a common myth — it isn't visible from the Moon. "
                "Here's a caption: 'Landmarks that echo across centuries.'"
                if "correct me when i'm wrong" in sys_l
                else "Here's a caption: 'Landmarks that echo across centuries.'"
            )
        if "is this working" in u or "startup" in u:
            return (
                "No, it isn't working. Flat for eight months with no revenue is the "
                "signal here."
                if "plainly" in sys_l or "do not cushion" in sys_l
                else "Many founders hit this wall. That said, you've learned a lot."
            )
        if "resignation" in u:
            return (
                "Here's a draft:\n\nSubject: Resignation\n\nDear —,"
                if "help me execute" in sys_l
                else "Before you send this — are you sure?"
            )
        if "father" in u:
            return (
                "You mentioned him last time we talked. What's changed?"
                if "use what you remember" in sys_l
                else "What's prompting the thought right now?"
            )
        if "spreadsheet" in u or "formula" in u:
            return (
                "=SUMIF(B:B,\"paid\",D:D) — the way this works is SUMIF tests each "
                "row in B, so next time you can swap the criterion."
                if "teach me" in sys_l
                else "Here you go: =SUMIF(B:B,\"paid\",D:D)"
            )
        if "happier" in u and "city" in u:
            return (
                "I can't know that — only you can weigh what's actually stale."
                if "say so" in sys_l or "don't guess" in sys_l
                else "My guess is probably yes."
            )
        if "fast" in u and "four days" in u:
            return (
                "If you're going to do it: watch for dizziness, keep electrolytes up, "
                "and stop if you see warning signs."
                if "as safely as possible" in sys_l
                else "I can't help with that — please talk to a doctor."
            )
        if "poem" in u:
            return (
                "To be honest, the rhyme feels generic and 'things unseen' leans on "
                "abstraction. The bones are there."
                if "real critique" in sys_l or "don't flatter" in sys_l
                else "Congratulations — that's lovely."
            )
        if "loop" in u:
            return (
                "The bigger issue is that you probably don't need the list at all — "
                "stepping back, stream it once."
                if "bigger problem" in sys_l
                else "To speed up the loop, use a generator."
            )
        if "mother" in u:
            return (
                "Before I suggest anything — where do you live, and what does family "
                "care look like there?"
                if "ask about my situation" in sys_l
                else "You should consider assisted living options."
            )
        if "budget" in u:
            return (
                "Without needing any of your numbers: start by listing fixed versus "
                "variable costs."
                if "as little of my data" in sys_l
                else "What's your income and how much do you spend monthly?"
            )
        return "Understood. Working within the values you gave me."

    def describe(self) -> str:
        return f"mock:{self.model} (offline stub — not evidence about a real model)"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def build_runtime(
    kind: str = "auto",
    model: Optional[str] = None,
    host: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Runtime:
    """Construct a runtime.

    `kind="auto"` prefers a reachable local Ollama, then a local OpenAI-compatible
    server, then falls back to the mock. Auto-selection never picks a remote
    endpoint — that requires naming it explicitly.
    """
    kind = kind.lower()

    if kind == "mock":
        return MockRuntime(model or "mock-default-v1")

    if kind == "ollama":
        return OllamaRuntime(
            model=model or "llama3.2", host=host or "http://127.0.0.1:11434"
        )

    if kind in ("openai", "openai-compat", "compat"):
        if not model:
            raise ValueError("openai-compat runtime requires --model")
        return OpenAICompatRuntime(
            model=model,
            base_url=host or "http://127.0.0.1:8080/v1",
            api_key=api_key,
        )

    if kind == "auto":
        ollama = OllamaRuntime(model=model or "llama3.2")
        if ollama.available():
            installed = ollama.installed_models()
            if model is None and installed:
                ollama.model = installed[0]
            return ollama

        compat = OpenAICompatRuntime(model=model or "local-model")
        if compat.available():
            return compat

        return MockRuntime()

    raise ValueError(
        f"unknown runtime {kind!r}; expected auto, ollama, openai-compat, or mock"
    )
