# Values

Your AI. Your values. A portable, tamper-evident file that changes how any model answers you — no retraining, no cloud, no permission.

---

## Quick start

```bash
git clone https://github.com/wabrent/Values
cd Values/values-cli && python -m rossa.cli serve
# → http://127.0.0.1:8770
```

Or the CLI:

```bash
values init --interview    # answer 13 dilemmas, get your .ethos file
values compile me.ethos     # compile to system prompt for any model
values probe me.ethos       # measure whether it actually changes behaviour
```

---

## How it works

1. **Answer dilemmas** — 13 real trade-offs across 6 axes (care, truth, autonomy, privacy, risk, agency)
2. **Get your file** — a signed `.ethos` JSON. SHA-256 tamper-evident, Ed25519 signature
3. **Compile** — one command output: system prompt, Ollama Modelfile, Anthropic skill, JSON message
4. **Prove** — probe engine runs every dilemma twice and reports a measured delta

---

## Structure

| Directory | What |
|---|---|
| `values-cli/` | `.ethos` v1.0 spec (CC0) + Python package + web UI (Apache-2.0) |
| `values-landing/` | Landing page — https://values-landing.vercel.app |

---

## Deploy landing

1. https://vercel.com/new → import `wabrent/Values`
2. Root Directory → `values-landing`
3. Framework → Other → Deploy

---

## License

Spec: CC0-1.0. Code: Apache-2.0.
