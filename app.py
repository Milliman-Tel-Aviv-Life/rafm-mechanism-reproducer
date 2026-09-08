"""
RAFM Mechanism Reproducer — Streamlit app.
Covers Stage 1 → Checkpoint 1 → Stage 2 → Checkpoint 2.
"""

from pathlib import Path

import streamlit as st

from src.rafm_reproducer.config import (
    available_clients,
    available_models,
    load_stage_cfg,
    stage_model_map,
)
from src.rafm_reproducer.llm_client import get_client
from src.rafm_reproducer.orchestrator import (
    apply_checkpoint1_approve,
    apply_checkpoint1_correct,
    apply_checkpoint2_approve,
    apply_checkpoint2_correct,
    start_run,
    trigger_stage2,
    trigger_stages_3_to_7,
)
from src.rafm_reproducer.schemas.user_input import UserPrompt

# ── defaults ──────────────────────────────────────────────────────────────────
# Pre-selected company; the list itself is discovered from docs/.
_DEFAULT_CLIENT = "Ayalon"

# ── helpers ───────────────────────────────────────────────────────────────────

_AUTO = "Auto — per stage (config.yaml)"


def _override() -> str | None:
    """The sidebar model, or None when each stage keeps its configured model."""
    choice = st.session_state.get("model_choice")
    return None if choice in (None, _AUTO) else choice


def _cfg_for_stage(stage: str) -> dict:
    return load_stage_cfg(stage, _override())


def _confidence_badge(conf: str) -> str:
    return {"high": "🟢 high", "medium": "🟡 medium", "low": "🔴 low"}.get(conf, conf)


# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="RAFM Reproducer", layout="wide", page_icon="📊")

st.markdown("""
<style>
    /* Slightly larger base text */
    html, body, [class*="css"] { font-size: 15px; }
    /* Bigger text areas */
    textarea { font-size: 14px !important; }
    /* Checkpoint bullet lists */
    .stMarkdown li { font-size: 15px; margin-bottom: 4px; }
</style>
""", unsafe_allow_html=True)

st.title("📊 RAFM Mechanism Reproducer")

# ── session state bootstrap ───────────────────────────────────────────────────
if "state" not in st.session_state:
    st.session_state.state = None
if "phase" not in st.session_state:
    st.session_state.phase = "form"   # form | cp1 | stage2_running | cp2 | generating | done
if "gen_stage" not in st.session_state:
    st.session_state.gen_stage = 3
if "gen_error" not in st.session_state:
    st.session_state.gen_error = None
if "gen_timings" not in st.session_state:
    st.session_state.gen_timings = {}   # {stage_n: elapsed_seconds}
if "gen_stage_start" not in st.session_state:
    st.session_state.gen_stage_start = None

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Configuration")

    _clients = available_clients()
    if not _clients:
        st.error(
            "No client hierarchy found in docs/. Convert an audit report first:\n"
            "`python scripts/extract_hierarchy_from_pdf.py \"<report.pdf>\" <Client>`"
        )
        st.stop()
    _names = list(_clients)
    company = st.selectbox(
        "Company",
        options=_names,
        index=_names.index(_DEFAULT_CLIENT) if _DEFAULT_CLIENT in _names else 0,
        key="company",
        help="Which client model the analysis runs against. The High/Low JSON "
             "pair is picked from docs/ accordingly.",
        disabled=st.session_state.state is not None,
    )
    _high_default, _low_default = _clients[company]
    if st.session_state.state is not None:
        st.caption("Locked for this run — start a new run to switch company.")

    model_choice = st.selectbox(
        "Model",
        options=[_AUTO] + available_models(),
        index=0,
        key="model_choice",
        help="Auto runs each stage on the model assigned to it in config.yaml. "
             "Pick a single model to force it on every stage.",
    )
    _stage_map = stage_model_map()
    st.caption(
        "Per stage: "
        + " · ".join(f"{s.replace('stage', 'S')} `{m}`" for s, m in _stage_map.items())
        if model_choice == _AUTO
        else f"Forcing `{model_choice}` on every stage."
    )

    if st.session_state.state:
        st.divider()
        st.caption(f"Run: `{st.session_state.state.run_id}`")
        st.caption(f"Saved to: `{st.session_state.state.run_dir}`")

    if st.button("🔄 New run", use_container_width=True):
        st.session_state.state = None
        st.session_state.phase = "form"
        st.rerun()

    with st.expander("⚙️ Advanced"):
        high_path = st.text_input(
            "High JSON path", value=str(_high_default), key=f"high_{company}"
        )
        low_path = st.text_input(
            "Low JSON path", value=str(_low_default), key=f"low_{company}"
        )
        st.caption("Batch mode")
        batch_file = st.text_input("Corrections JSON (optional)", value="")

# ── load batch corrections if provided ───────────────────────────────────────
batch_corrections: dict = {}
if batch_file and Path(batch_file).exists():
    import json
    batch_corrections = json.loads(Path(batch_file).read_text(encoding="utf-8"))

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: form — enter user request and run Stage 1
# ═════════════════════════════════════════════════════════════════════════════
if st.session_state.phase == "form":
    st.header("Step 1 — Describe the mechanism")
    st.caption(
        "Describe in plain language what mechanism you want to reproduce: "
        "what it computes, which products it applies to, and which model groups are involved. "
        "Be as precise as you can — the more context you give, the better the result."
    )
    with st.form("user_prompt_form"):
        text = st.text_area(
            "Your description",
            placeholder=(
                "e.g. I want to reproduce the variable management fee deduction for "
                "old-adif participating products (par_nonpar = P, old_adif = true). "
                "The fee is only charged when there is no outstanding BOR debt. "
                "Models involved: adif_cflow, fund_cflow."
            ),
            height=280,
        )
        submitted = st.form_submit_button("▶ Run Stage 1", type="primary", use_container_width=True)

    if submitted:
        if not text.strip():
            st.error("Please describe the mechanism before running.")
        else:
            user_prompt = UserPrompt(text=text)
            cfg = _cfg_for_stage("stage1")
            client = get_client(cfg)

            with st.spinner("Running Stage 1…"):
                st.markdown("""
**What is happening in the background:**
- Your description is sent to the LLM with an actuarial framing prompt
- The model interprets the mechanism in RAFM terms
- It identifies likely model areas (branches, cashflow types, product filters)
- It flags assumptions and ambiguities for your review
- *This typically takes 15–30 seconds*
""")
                try:
                    state = start_run(
                        high_json_path=Path(high_path),
                        low_json_path=Path(low_path),
                        model_name=cfg["model"],
                        user_prompt=user_prompt,
                        client=client,
                        cfg=cfg,
                    )
                    st.session_state.state = state
                    st.session_state.phase = "cp1"
                    st.rerun()
                except Exception as e:
                    st.error(f"Stage 1 failed: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: cp1 — Checkpoint 1: review Stage 1 output
# ═════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "cp1":
    state = st.session_state.state
    out = state.stage1_output
    st.header("Step 2 — Checkpoint 1: review the interpretation")

    if out and out.halt_reason:
        st.error(f"**Stage 1 halted:** {out.halt_reason}")
        st.info("Refine your request and start a new run.")
        st.stop()

    if out is None:
        st.error("No Stage 1 output found. Something went wrong.")
        st.stop()

    # ── Confirmed request (top of page) ──────────────────────────────────────
    st.markdown("""
<div style="background:#e8f4f8;border-left:4px solid #1f77b4;padding:14px 18px;border-radius:4px;margin-bottom:16px">
<span style="font-size:13px;color:#555;font-weight:600;text-transform:uppercase;letter-spacing:.05em">
The model understood your request as:</span><br>
<span style="font-size:16px;color:#1a1a2e;font-weight:500;line-height:1.5">{confirmed}</span>
</div>
""".format(confirmed=out.confirmed_request), unsafe_allow_html=True)

    st.write(f"**Confidence:** {_confidence_badge(out.confidence)}")
    st.divider()

    # ── Two-column layout ────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("📋 Full interpretation")
        st.info(out.interpretation)

        st.subheader("🌿 Relevant model areas")
        for b in out.relevant_branches:
            st.markdown(f"- {b}")

    with col_right:
        st.subheader("💡 Assumptions")
        for a in out.assumptions:
            st.markdown(f"- {a}")

        if out.ambiguities:
            st.subheader("⚠️ Ambiguities to resolve")
            for amb in out.ambiguities:
                st.warning(amb)
    st.divider()

    if "checkpoint1" in batch_corrections and batch_corrections["checkpoint1"]["action"] == "approve":
        st.info("Batch mode: auto-approving Checkpoint 1.")
        state = apply_checkpoint1_approve(state)
        st.session_state.state = state
        st.session_state.phase = "stage2_running"
        st.rerun()

    col_a, col_c = st.columns(2)
    with col_a:
        if st.button("✅ Approve — run Stage 2", type="primary", use_container_width=True):
            state = apply_checkpoint1_approve(state)
            st.session_state.state = state
            st.session_state.phase = "stage2_running"
            st.rerun()

    with col_c:
        with st.expander("✏️ Correct the interpretation"):
            correction = st.text_area("Your correction:", height=100, key="cp1_correction")
            if "checkpoint1" in batch_corrections and batch_corrections["checkpoint1"]["action"] == "correct":
                correction = batch_corrections["checkpoint1"].get("correction", "")
                st.caption(f"Batch correction: {correction}")
            if st.button("Submit correction and re-run Stage 1", key="cp1_submit"):
                if not correction.strip():
                    st.error("Please enter a correction.")
                else:
                    cfg = _cfg_for_stage("stage1")
                    client = get_client(cfg)
                    with st.spinner("Re-running Stage 1 with your correction…"):
                        state = apply_checkpoint1_correct(state, correction, client, cfg)
                    st.session_state.state = state
                    st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: stage2_running — trigger Stage 2 then move to cp2
# ═════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "stage2_running":
    state = st.session_state.state
    st.header("Step 3 — Running Stage 2…")
    with st.spinner("Scanning the hierarchy and selecting formulas…"):
        st.markdown("""
**What is happening in the background:**
- The full model hierarchy (9 000+ formulas) is loaded and compacted
- The LLM scans it using the approved interpretation and keyword hints
- It selects the formulas that **define** the mechanism (vs those that merely consume it)
- It follows dependency chains and records why each formula was included
- *This typically takes 30–60 seconds*
""")
        try:
            cfg = _cfg_for_stage("stage2")
            client = get_client(cfg)
            state = trigger_stage2(state, client, cfg)
            st.session_state.state = state
            st.session_state.phase = "cp2"
            st.rerun()
        except Exception as e:
            st.error(f"Stage 2 failed: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: cp2 — Checkpoint 2: review Stage 2 formula selection
# ═════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "cp2":
    state = st.session_state.state
    out = state.stage2_output
    st.header("Step 4 — Checkpoint 2: review the formula selection")

    if out is None:
        st.error("No Stage 2 output found. Something went wrong.")
        st.stop()

    if state.stage2_validation_warnings:
        st.warning(
            "**Validation warnings** (output still shown — review carefully):\n"
            + "\n".join(f"- {w}" for w in state.stage2_validation_warnings)
        )

    st.subheader(f"Selected formulas — {len(out.selected)} formula(s)")
    for f in out.selected:
        with st.expander(f"[{f.numero}] {f.titre}", expanded=False):
            st.write(f"**Role:** {f.role_one_line}")
            st.write(f"**Why defining:** {f.why_defining}")
            if f.branches_in_scope:
                st.write(f"**Branches in scope:** {', '.join(f.branches_in_scope)}")

    if out.consumers_for_info:
        st.subheader(f"Consumers (for information — {len(out.consumers_for_info)})")
        for c in out.consumers_for_info:
            st.write(f"• [{c.numero}] **{c.titre}**: {c.consumes_what}")

    if out.dependencies_followed:
        st.subheader("Dependencies followed")
        for dep in out.dependencies_followed:
            st.write(f"• [{dep.from_numero}] → [{dep.to_numero}]: {dep.reason}")

    if out.concerns:
        st.subheader("⚠️ Concerns")
        for c in out.concerns:
            st.warning(c)

    st.divider()

    if "checkpoint2" in batch_corrections and batch_corrections["checkpoint2"]["action"] == "approve":
        st.info("Batch mode: auto-approving Checkpoint 2.")
        state = apply_checkpoint2_approve(state)
        st.session_state.state = state
        st.session_state.phase = "generating"
        st.rerun()

    col_a, col_c = st.columns(2)
    with col_a:
        if st.button("✅ Approve selection — generate Excel", type="primary", use_container_width=True):
            state = apply_checkpoint2_approve(state)
            st.session_state.state = state
            st.session_state.phase = "generating"
            st.session_state.gen_stage = 3
            st.session_state.gen_error = None
            st.session_state.gen_timings = {}
            st.session_state.gen_stage_start = None
            st.rerun()

    with col_c:
        with st.expander("✏️ Correct the selection"):
            correction = st.text_area("Your correction:", height=100, key="cp2_correction")
            if "checkpoint2" in batch_corrections and batch_corrections["checkpoint2"]["action"] == "correct":
                correction = batch_corrections["checkpoint2"].get("correction", "")
                st.caption(f"Batch correction: {correction}")
            if st.button("Submit correction and re-run Stage 2", key="cp2_submit"):
                if not correction.strip():
                    st.error("Please enter a correction.")
                else:
                    cfg = _cfg_for_stage("stage2")
                    client = get_client(cfg)
                    with st.spinner("Re-running Stage 2 with your correction…"):
                        state = apply_checkpoint2_correct(state, correction, client, cfg)
                    st.session_state.state = state
                    st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: generating — one stage per rerun, full transparency
# ═════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "generating":
    import time as _time
    import json as _json
    import traceback as _tb
    from src.rafm_reproducer.stages.stage3 import run_stage3
    from src.rafm_reproducer.stages.stage4 import run_stage4
    from src.rafm_reproducer.stages.stage5 import run_stage5
    from src.rafm_reproducer.stages.stage6 import run_stage6
    from src.rafm_reproducer.stages.stage7 import run_stage7

    state = st.session_state.state
    current = st.session_state.gen_stage
    timings = st.session_state.gen_timings

    st.header("Step 5 — Generating Excel workbook")
    st.divider()

    def _stage_model(n: int) -> str:
        return _cfg_for_stage(f"stage{n}")["model"]

    _STAGE_META = {
        3: ("Stage 3", "Load source code from Low JSON",            False, "—"),
        4: ("Stage 4", "Decompose formulas analytically",           True,  _stage_model(4)),
        5: ("Stage 5", "Generate Excel specification",              True,  _stage_model(5)),
        6: ("Stage 6", "Self-review vs source code",                True,  _stage_model(6)),
        7: ("Stage 7", "Build .xlsx workbook",                      False, "—"),
    }

    # ── Pipeline progress ─────────────────────────────────────────────────────
    for n, (tag, desc, is_llm, mdl) in _STAGE_META.items():
        elapsed = timings.get(n)
        if n < current:
            t_str = f"  *(done in {elapsed:.1f}s)*" if elapsed else ""
            st.success(f"**{tag}** — {desc}{t_str}")
        elif n == current and not st.session_state.gen_error:
            st.info(f"**{tag}** — {desc} ⏳ running on **{mdl}**…")
        elif n == current and st.session_state.gen_error:
            st.error(f"**{tag}** — {desc}  ❌ failed")
        else:
            st.markdown(
                f"<span style='color:#aaa;font-size:14px'>**{tag}** — {desc} &nbsp;|&nbsp; {mdl}</span>",
                unsafe_allow_html=True,
            )

    # ── What we're about to send ───────────────────────────────────────────────
    if not st.session_state.gen_error:
        st.divider()
        if current == 3:
            n_sel = len(state.stage2_output.selected) if state.stage2_output else "?"
            st.markdown(f"**Stage 3 input:** {n_sel} selected formula(s) → looking up source code in Low JSON")
        elif current == 4:
            n_forms = len(state.stage3_enriched)
            total_chars = sum(len(ef.contenu or "") for ef in state.stage3_enriched)
            st.markdown(
                f"**Stage 4 input:** {n_forms} formula(s) — "
                f"{total_chars:,} chars of source code sent to GPT-5.5 for decomposition"
            )
            with st.expander("Formulas being analysed"):
                for ef in state.stage3_enriched:
                    st.markdown(f"- **[{ef.numero}]** {ef.titre} — {len(ef.contenu or '')} chars")
        elif current == 5:
            s4_json = state.stage4_output.model_dump_json() if state.stage4_output else "{}"
            st.markdown(
                f"**Stage 5 input:** Stage 4 decomposition ({len(s4_json):,} chars) + "
                f"user request → GPT-5.5 builds the full Excel spec"
            )
            if state.stage4_output and state.stage4_output.reasoning:
                with st.expander("Stage 4 reasoning (sent as context to Stage 5)"):
                    st.markdown(state.stage4_output.reasoning)
        elif current == 6:
            s5_json = state.stage5_output.model_dump_json() if state.stage5_output else "{}"
            n_calcs = len(state.stage5_output.calculations) if state.stage5_output else "?"
            st.markdown(
                f"**Stage 6 input:** Excel spec ({n_calcs} columns, {len(s5_json):,} chars) + "
                f"source code → self-review for omissions"
            )
            if state.stage5_output and state.stage5_output.reasoning:
                with st.expander("Stage 5 reasoning (expand to read)"):
                    st.markdown(state.stage5_output.reasoning)
        elif current == 7:
            st.markdown("**Stage 7:** purely mechanical — no LLM call, building .xlsx from the spec")

    # ── Error + Retry/Abandon ─────────────────────────────────────────────────
    if st.session_state.gen_error:
        st.divider()
        err_msg = st.session_state.gen_error
        is_network = any(k in err_msg for k in ("Connection error", "getaddrinfo", "ConnectError"))
        if is_network:
            st.error("**Network / VPN error** — verify your connection then click Retry.")
        else:
            with st.expander("Error details"):
                st.code(err_msg)
        col_r, col_n = st.columns(2)
        with col_r:
            if st.button("🔄 Retry this stage", type="primary", use_container_width=True):
                st.session_state.gen_error = None
                st.rerun()
        with col_n:
            if st.button("✖ Abandon", use_container_width=True):
                for k in ("state", "phase", "gen_stage", "gen_error", "gen_timings", "gen_stage_start"):
                    st.session_state[k] = None if k in ("state", "gen_error", "gen_stage_start") else (
                        "form" if k == "phase" else (3 if k == "gen_stage" else {})
                    )
                st.rerun()
        st.stop()

    # ── Run current stage ─────────────────────────────────────────────────────
    _stage_cfgs = {n: _cfg_for_stage(f"stage{n}") for n in (4, 5, 6)}
    _stage_clients = {n: get_client(c) for n, c in _stage_cfgs.items()}

    t0 = _time.time()
    try:
        if current == 3:
            state = run_stage3(state)
        elif current == 4:
            state = run_stage4(state, _stage_clients[4], _stage_cfgs[4])
        elif current == 5:
            state = run_stage5(state, _stage_clients[5], _stage_cfgs[5])
        elif current == 6:
            state = run_stage6(state, _stage_clients[6], _stage_cfgs[6])
        elif current == 7:
            state = run_stage7(state)

        st.session_state.gen_timings[current] = _time.time() - t0
        st.session_state.state = state
        if current < 7:
            st.session_state.gen_stage = current + 1
        else:
            st.session_state.phase = "done"
            st.session_state.gen_stage = 3
        st.rerun()

    except Exception as e:
        st.session_state.gen_timings[current] = _time.time() - t0
        st.session_state.state = state
        st.session_state.gen_error = _tb.format_exc()
        st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
# PHASE: done — Excel ready for download
# ═════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "done":
    state = st.session_state.state
    st.success("✅ Excel workbook generated successfully!")

    # ── Download button ───────────────────────────────────────────────────────
    if state.output_xlsx_path and state.output_xlsx_path.exists():
        with open(state.output_xlsx_path, "rb") as f:
            xlsx_bytes = f.read()
        mechanism_name = (
            state.stage5_output.mechanism_name
            if state.stage5_output
            else "mechanism"
        )
        filename = f"{mechanism_name.replace(' ', '_')}.xlsx"
        st.download_button(
            label="⬇️ Download Excel workbook",
            data=xlsx_bytes,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    else:
        st.warning("Output file not found — check the run folder.")

    st.divider()
    st.write(f"**Run folder:** `{state.run_dir}`")

    # ── Summary ───────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if state.stage5_output:
            st.subheader("📊 Workbook summary")
            spec = state.stage5_output
            st.write(f"**Mechanism:** {spec.mechanism_name}")
            st.write(f"**Scope:** {spec.scope_description}")
            st.write(f"**Periods:** {spec.scenario.n_periods}")
            st.write(f"**Input parameters:** {len(spec.inputs)}")
            st.write(f"**Calculation columns:** {len(spec.calculations)}")

    with col2:
        if state.stage6_output and state.stage6_output.omissions:
            st.subheader("⚠️ Omissions & simplifications")
            for omission in state.stage6_output.omissions:
                icon = "⚠️" if omission.kind == "concerning" else "ℹ️"
                st.write(f"{icon} **[{omission.kind}]** {omission.summary}")
            if state.stage6_output.overall_concerns:
                st.divider()
                for c in state.stage6_output.overall_concerns:
                    st.warning(c)

    # ── LLM Reasoning transparency ────────────────────────────────────────────
    st.divider()
    st.subheader("🧠 LLM Reasoning (GPT-5.5)")

    if state.stage4_output and state.stage4_output.reasoning:
        with st.expander("Stage 4 — Formula decomposition reasoning", expanded=False):
            st.markdown(state.stage4_output.reasoning)

    if state.stage5_output and state.stage5_output.reasoning:
        with st.expander("Stage 5 — Excel specification reasoning", expanded=True):
            st.markdown(state.stage5_output.reasoning)
