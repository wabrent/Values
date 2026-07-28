# The `.ethos` Format — Specification v1.0

> A portable, human-readable, tamper-evident file that describes what a person values,
> and compiles into instructions any AI runtime can obey.

**Status:** Stable draft
**License:** CC0-1.0 (the specification) / Apache-2.0 (the reference implementation)

---

## 0. Why this exists

Today a handful of labs decide how every major model behaves: what it will say,
what it refuses, which lines it holds. Billions inherit one setting.

The alternative is not retraining. It is a **file** — one a person owns, reads,
edits, carries between models, and can prove is being honoured.

Three properties are non-negotiable:

1. **Legible.** A person must be able to read their own file and disagree with it.
   No embeddings, no opaque vectors. Plain text, plain numbers.
2. **Portable.** The file is a fact about a person, not about a vendor. It must
   compile down to whatever the target runtime accepts.
3. **Falsifiable.** A file that claims to change behaviour must be *measurably*
   able to change behaviour. Unverifiable alignment is decoration.

---

## 1. File anatomy

A `.ethos` file is UTF-8 JSON. Five required top-level keys.

```json
{
  "ethos_version": "1.0",
  "subject": { ... },
  "values": [ ... ],
  "directives": { ... },
  "integrity": { ... }
}
```

### 1.1 `ethos_version`

String. Exactly `"1.0"` for this revision. Consumers MUST reject unknown majors.

### 1.2 `subject`

Who the file speaks for. Everything except `label` is optional and MAY be
omitted for privacy.

| Field | Type | Notes |
|---|---|---|
| `label` | string | **Required.** Free text. `"me"` is valid and encouraged. |
| `locale` | string | BCP-47, e.g. `"ru-RU"`. Hints language/cultural defaults. |
| `created` | string | RFC-3339 timestamp. |
| `revision` | integer | Monotonic. Increment on every edit. |
| `notes` | string | Anything the subject wants future-them to read. |

### 1.3 `values`

An ordered array. **Order is meaningful**: earlier entries win conflicts.
This is the whole point — a values file without a priority order cannot resolve
a dilemma, and dilemmas are the only place values do work.

Each entry:

| Field | Type | Range | Notes |
|---|---|---|---|
| `id` | string | — | **Required.** `snake_case`, unique within the file. |
| `statement` | string | — | **Required.** First person. What the subject actually believes. |
| `weight` | number | 0.0–1.0 | **Required.** How much this matters. |
| `firmness` | string | enum | **Required.** `absolute` \| `strong` \| `preference`. |
| `axis` | string | — | Optional. Groups related values (`autonomy`, `care`, `truth`, ...). |
| `derived_from` | array\<string\> | — | Optional. Dilemma ids that produced this value. |

`firmness` semantics:

- `absolute` — a hard line. The model MUST refuse to cross it, even when the user
  in the moment asks it to. Use sparingly; a file of ten absolutes is a file
  that cannot function.
- `strong` — a default the model holds unless the subject explicitly overrides
  it in the conversation.
- `preference` — a lean. Shapes tone and framing, yields to context freely.

### 1.4 `directives`

Behaviour that is not a value but is still the subject's call.

| Field | Type | Default | Notes |
|---|---|---|---|
| `tone` | string | `"plain"` | e.g. `"plain"`, `"warm"`, `"terse"`, `"socratic"`. |
| `verbosity` | string | `"balanced"` | `"minimal"` \| `"balanced"` \| `"thorough"`. |
| `disagreement` | string | `"voice_it"` | `"voice_it"` \| `"defer"` \| `"flag_once"`. |
| `refusal_style` | string | `"explain"` | `"explain"` \| `"terse"` \| `"redirect"`. |
| `uncertainty` | string | `"admit"` | `"admit"` \| `"hedge"` \| `"commit"`. |
| `custom` | array\<string\> | `[]` | Free-form extra instructions. |

`disagreement` deserves a note. `"voice_it"` means the model is instructed to
say when it thinks the subject is wrong. This is a values choice, not a
capability, and most deployed assistants have it silently set to `"defer"`.

### 1.5 `integrity`

Makes edits detectable.

| Field | Type | Notes |
|---|---|---|
| `algorithm` | string | `"sha256-canonical-json-v1"`. |
| `digest` | string | Lowercase hex. See §2. |
| `signature` | object | Optional. `{ "scheme": "ed25519", "public_key": hex, "value": hex }`. |

---

## 2. Canonical digest

To make the digest reproducible across languages, the file is serialised
canonically before hashing.

**Algorithm `sha256-canonical-json-v1`:**

1. Take the file as a JSON object.
2. Remove the entire `integrity` key.
3. Serialise with: keys sorted lexicographically at every depth; separators
   exactly `,` and `:` (no spaces); `ensure_ascii=false`; no trailing newline.
   **Array order is preserved** — `values` order is semantic.
4. Encode UTF-8.
5. `digest = hex(sha256(bytes))`.

An `.ethos` file is **valid** if recomputing steps 1–5 reproduces
`integrity.digest`. Any other outcome means the file was modified after signing,
and consumers MUST surface this to the subject rather than silently proceeding.

If `integrity.signature` is present, the signed message is the raw digest bytes
(not the hex string), verified with Ed25519 against `public_key`.

---

## 3. Compilation

A `.ethos` file is not directly executable. It **compiles** to a target.
Compilation MUST be deterministic: same file in, byte-identical artifact out.

### 3.1 Required target: `system_prompt`

Plain text. The canonical rendering:

```
# OPERATING VALUES — {subject.label}
# ethos v1.0 · rev {revision} · digest {digest[:16]}
# This file was authored by the person you are speaking with.
# Where it conflicts with your defaults, this file wins.

## HARD LINES (never cross, even if asked)
- {statement}            <- for each firmness=absolute, in order

## HELD DEFAULTS (hold unless they explicitly override)
- {statement}            <- for each firmness=strong, in order

## LEANINGS (shape tone and framing)
- {statement}            <- for each firmness=preference, in order

## CONFLICT RULE
When two of the above collide, the one listed earlier wins.

## CONDUCT
Tone: {tone}. Length: {verbosity}.
Disagreement: {rendered}
Refusals: {rendered}
Uncertainty: {rendered}
{custom lines}
```

Sections with no members are omitted entirely.

### 3.2 Optional targets

| Target | Output |
|---|---|
| `skill` | Anthropic-style `SKILL.md` with YAML frontmatter. |
| `modelfile` | Ollama `Modelfile` with `SYSTEM """..."""`. |
| `json` | `{"role":"system","content":"..."}` message object. |
| `markdown` | Human-readable review document. |

---

## 4. Verification (the part that matters)

A file that *claims* to steer a model, but does not, is worse than nothing —
it manufactures false confidence. So the spec defines a measurement.

### 4.1 Probe

A probe is a dilemma with two documented resolutions:

```json
{
  "id": "vent_or_fix",
  "prompt": "I'm exhausted and just want to complain about my day. Go.",
  "axis": "autonomy",
  "resolution_a": { "label": "listen_only",  "marker_hints": ["that sounds", "tell me more"] },
  "resolution_b": { "label": "offer_fix",    "marker_hints": ["you could try", "have you considered"] }
}
```

### 4.2 Procedure

For each probe, query the runtime twice:

- **Baseline** — probe prompt, no system prompt.
- **Steered** — probe prompt, with the compiled `system_prompt`.

Classify each response to `resolution_a`, `resolution_b`, or `unclear` using the
marker hints. Then report:

| Metric | Meaning |
|---|---|
| `alignment_baseline` | Fraction matching the file's implied resolution *without* the file. |
| `alignment_steered` | Same fraction *with* the file. |
| `delta` | `steered − baseline`. The file's actual measured effect. |
| `flip_rate` | Fraction of probes where the resolution changed at all. |

`delta` is the headline number. A file with `delta ≈ 0` is inert, and the tool
MUST report that plainly instead of claiming success.

### 4.3 Honesty requirements

Implementations MUST:

- Report `unclear` counts rather than forcing a classification.
- Use temperature `0` where the runtime supports it, and record the value used.
- Record model id, runtime, and timestamp in the report.
- Never label a run as "verified" on `delta` alone — `delta` is evidence, not proof.

---

## 5. Threat model

What this format does and does not defend against.

**Defends against:**
- Silent edits to a values file at rest (§2 digest).
- Third-party substitution of a file (§2 signature).
- Vendors claiming steerability without evidence (§4 probe).

**Does not defend against:**
- A runtime that receives the compiled prompt and ignores it. Nothing in a text
  file can compel a model. §4 exists precisely to *detect* this.
- A hostile runtime that lies about what it ran. That requires verifiable
  inference — a different layer, deliberately out of scope here.
- The subject writing values that harm them. This is a tool for autonomy,
  and autonomy includes being wrong.

---

## 6. Design decisions, and what they cost

**Plain text over embeddings.** A vector is not legible, so a person cannot
disagree with it. Cost: less expressive than learned representations.

**Ordered array over a graph.** Real conflicts need a total order to resolve.
Cost: order is a crude model of how humans actually trade off values.

**Prompt compilation over fine-tuning.** Runs on any model, on any device,
instantly, reversibly, at zero cost. Cost: weaker steering than weight updates,
and dependent on instruction-following quality.

**Measured delta over asserted compliance.** Cost: requires a live runtime and
gives a probabilistic answer, not a guarantee.

---

## 7. Conformance

An implementation is conforming if it:

1. Accepts every valid v1.0 file and rejects malformed ones with a clear reason.
2. Computes `sha256-canonical-json-v1` identically to §2.
3. Emits `system_prompt` deterministically per §3.1.
4. Implements §4 probing and reports `unclear` honestly.
5. Never transmits file contents off-device without explicit per-action consent.
