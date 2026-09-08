# RAFM Mechanism Reproducer

Takes a parsed RAFM actuarial model and a user description of a mechanism,
and produces a functional Excel calculation an actuary can understand and re-derive.

## Setup

```bash
pip install -e .
cp .env.example .env
# edit .env if you need to override config.yaml values
```

## Run

```bash
streamlit run app.py
```

## Required data files (not in this repo)

The High/Low JSON pairs are committed in `docs/` (private repo, client data). The
reference Excel is not; place it in `docs/` before running:

| File | Used by |
|------|---------|
| `docs/Hierarchy_Menora_High.json` | Stage 2 formula selection |
| `docs/hierarchie_menora_Low.json` | Stage 3 verbatim code lookup |
| `docs/MF_BOR_model.xlsx` | Reference Excel |

Paths can be overridden in the sidebar under **Advanced**.

### Generating a new High/Low pair from an Audit Report PDF

If you have a "Willis Towers Watson RiskAgility FM Audit Report" PDF for a new client
model, `scripts/extract_hierarchy_from_pdf.py` parses it into the same High/Low JSON
shape:

```bash
pip install -e ".[pdf]"   # pulls in pymupdf, only needed for this script
python scripts/extract_hierarchy_from_pdf.py "<path to AuditReport.pdf>" <ClientName>
```

This writes `docs/Hierarchy_<ClientName>_High.json` and
`docs/hierarchie_<clientname>_Low.json`, which you then point the sidebar's Advanced
path fields at. `pymupdf` is not part of the app's normal dependencies — only installed
when you actually need to extract a new pair.

### Refreshing the Formulas chapter from a partial print

The full report generator truncates long formula bodies (on the 2020 Menora
report, ~400 of 3 006 formulas stopped mid-body). A fresh print of just the
Formulas appendix (chapter 8, e.g. via "Microsoft Print to PDF" on a page
range) carries complete bodies but none of the chapters Stage 2 needs for the
model map. Graft it onto the existing pair instead of replacing it:

```bash
python scripts/merge_partial_hierarchy.py Menora "<path to partial AuditReport.pdf>"
```

This keeps chapters 1-6 (and 8.3/8.4) from the existing JSONs, replaces the
sections the print covers (7.1, 8.1, 8.2) with the freshly parsed ones, joins
the `->` operator that the print's line wrapping splits across lines, and
copies the previous pair to `docs/backup_<client>_<date>/` first.
`extract_hierarchy_from_pdf.py --partial` is the underlying parser mode: it
lets the outline start anywhere instead of at "1 Summary".

Current Menora pair: chapters 1-6 come from the 2020 full report, chapters 7-8
from the September 2026 print of the newer model ("RAFM New"). The two differ
slightly (e.g. `num_cf` became `num_cf_movement`; 26 formulas of the new print
have no entry in the old chapter 6), so a full regenerated report is still the
clean fix.

## Project structure

```
docs/                        Reference artifacts (High/Low JSON, reference Excel)
src/rafm_reproducer/
  config.py                  Load config.yaml + env overrides
  llm_client.py              AzureOpenAI + instructor wrapper
  artifacts.py               Save/load run artifacts
  orchestrator.py            Pipeline runner
  schemas/                   Pydantic models for every stage
  prompts/                   Prompt .txt files (edit without touching code)
  stages/                    One module per pipeline stage
  validators/                Post-LLM validation for stages 1 and 2
runs/                        Auto-created; one subfolder per run (gitignored)
```

## Pipeline (Stages 1-2 implemented; 3-7 stubs)

| Step | Type | What it does | Model |
|------|------|-------------|-------|
| Stage 1 | LLM | Understands the user request | `claude-sonnet-5` (effort low) |
| Checkpoint 1 | Human | Approve or correct interpretation | — |
| Stage 2 | LLM | Selects formulas from the High hierarchy | `claude-opus-4-8` (effort high) |
| Checkpoint 2 | Human | Approve or correct formula selection | — |
| Stage 3 | Mech | Looks up verbatim code from Low JSON | — |
| Stage 4 | LLM | Decomposes formulas analytically | `claude-opus-4-8` (effort xhigh) |
| Stage 5 | LLM | Produces Excel JSON spec | `claude-opus-4-8` (effort xhigh) |
| Stage 6 | LLM | Self-reviews for omissions | `gpt-5.6-terra` |
| Stage 7 | Mech | Generates Excel via openpyxl | — |

## Models

All models sit behind the same Tel Aviv APIM endpoint and key, but on **two
different routes**, picked automatically from the model name:

| Models | Route | Client |
|--------|-------|--------|
| `claude-*` | `<endpoint>/anthropic/v1/messages` | `anthropic` SDK, header `api-key` |
| everything else | Azure OpenAI chat-completions | `openai.AzureOpenAI` |

Both are wrapped in `instructor` so every stage gets a validated Pydantic object.
The Anthropic path uses `Mode.ANTHROPIC_REASONING_TOOLS` and adaptive thinking —
instructor's default `ANTHROPIC_TOOLS` mode silently disables extended thinking
(0 reasoning tokens and visibly worse answers), so don't change it back.

### How Stage 2 sees the model

Stage 2 can only select from what it is shown, so `to_compact_text` renders
**every title in the report** — chapters, sections, sub-sections, down to each
formula and variable name. Nothing is filtered, sampled or summarised.

The hierarchy is carried twice over, on purpose: each line is indented by its
depth, and the dotted numero is written in full (`8.2.1.1.1.4` sits under
`8.2.1.1.1`). The model cites the numero verbatim rather than rebuilding it, and
reads a title together with its ancestors — the same formula name recurs under
several model classes and only the path tells them apart. A MODEL MAP block up
front gives the object → class tree so the model knows *where* to look before it
looks for names.

What is compressed is repetition of form, never content: the chapter name is no
longer repeated on all 12k lines (it is recoverable from the numero) and the
model map is de-duplicated. On Menora that is ~109k tokens covering all 12 248
titles, against ~113k for the previous flat listing — which silently dropped the
2 056 titles of the Summary and Input Manager chapters and showed no hierarchy
at all.

### Time budgets, and why the Anthropic path streams

Wall-clock is driven by how much the model *writes*, not how much it reads:
Stage 2 digests a ~109k-token hierarchy in ~50s, while Stage 4 reads ~2.4k
tokens and takes longer, because it generates more.

Measured end-to-end on Menora:

| stage | model | effort | measured |
|-------|-------|--------|----------|
| 1 | claude-sonnet-5 | low | ~30s |
| 2 | claude-opus-4-8 | high | ~50s |
| 4 | claude-opus-4-8 | high | ~84s |
| 5 | claude-opus-4-8 | high | ~474s (7.9 min) |
| 6 | gpt-5.6-terra | — | ~30s |
| 7 | mechanical | — | 0.1s |

Two findings worth keeping:

**effort `xhigh` is a cliff, not a gradient.** On the Stage 4 prompt: low 48s,
medium 71s, high 81s, xhigh **over 600s** — 7x the time for marginally more
reasoning. No stage uses it. `high` buys 3 200 reasoning tokens for 33s more
than `low`, which is the deal worth taking.

**The Anthropic path must stream.** The APIM kills a long non-streamed request —
first as a read timeout, then as HTTP 500 "Internal gateway error". The same
prompt streams fine, so `_call_anthropic` drives the tool round-trip over
`messages.stream()` by hand: instructor's `create()` is non-streamed and its
`create_partial()` yields nothing in tool mode. Consequently `timeout_s` on that
route is an *idle* timeout, not a total budget — generation length is bounded by
`max_tokens` and `effort` instead.

Stage 5 is the one stage that will not fit 7 minutes. Dropping it to effort
`medium` makes it omit required schema fields (`mechanism_name`, `key_relation`)
and burn its retries failing, so it keeps `high` and ~8 minutes.

### Which model runs which stage

Set in `config.yaml` under `stage_models` (see the pipeline table above). The
heavy reasoning — reading RAFM's C-like source, decomposing it, and turning it
into an Excel architecture — goes to `claude-opus-4-8` at high effort. Stage 6
audits stages 4-5 and deliberately runs on a *different provider*, because a
model reviewing its own output shares its own blind spots. Stage 1 is a short
reading task and stays on the cheap model.

Overrides, first hit wins:

1. The sidebar model selector — picking a model forces it on every stage
   (`Auto` keeps the per-stage mapping).
2. `RAFM_MODEL_STAGE4` (etc.) in `.env`.
3. `stage_models` in `config.yaml`.
4. `RAFM_MODEL` in `.env`, then `llm_deployment`.
