"""
Stage 4 — LLM: decompose each selected formula analytically.
Reads verbatim source code from Stage 3 enriched output.
"""

from ..artifacts import save_json, save_text
from ..config import load_prompt
from ..llm_client import call_llm
from ..schemas.pipeline_state import PipelineState
from ..schemas.stage4 import Stage4Output
from ..validators.stage4 import validate_stage4

MAX_SEMANTIC_RETRIES = 2


def _build_user_prompt(state: PipelineState, validation_errors: list[str]) -> str:
    s1 = state.stage1_output
    parts = [
        "=== MECHANISM CONTEXT ===",
        f"Confirmed request: {s1.confirmed_request if s1 else ''}",
        f"Interpretation: {s1.interpretation if s1 else ''}",
        "",
        "=== SELECTED FORMULAS WITH SOURCE CODE ===",
    ]
    for ef in state.stage3_enriched:
        parts.append(f"\n--- [{ef.numero}] {ef.titre} ---")
        parts.append(ef.contenu if ef.contenu else "(no code)")

    if validation_errors:
        parts.append("\n\n=== VALIDATION ERRORS IN YOUR PREVIOUS OUTPUT — PLEASE FIX ===")
        for err in validation_errors:
            parts.append(f"- {err}")

    return "\n".join(parts)


def run_stage4(state: PipelineState, client, cfg: dict) -> PipelineState:
    assert state.stage3_enriched, "Stage 3 must complete before Stage 4"

    system = load_prompt("stage4_system")
    validation_errors: list[str] = []
    output: Stage4Output | None = None

    for attempt in range(MAX_SEMANTIC_RETRIES + 1):
        label = f"stage4_attempt{attempt + 1}"
        user = _build_user_prompt(state, validation_errors)

        save_text(state.run_dir, f"{label}_prompt_system", system)
        save_text(state.run_dir, f"{label}_prompt_user", user)

        output = call_llm(
            client=client,
            model=cfg["model"],
            system=system,
            user=user,
            response_model=Stage4Output,
            effort=cfg.get("effort"),
            max_tokens=cfg.get("max_tokens"),
        )

        save_json(state.run_dir, f"{label}_output", output)

        validation_errors = validate_stage4(output, state.stage3_enriched)
        save_json(state.run_dir, f"{label}_validation_errors", validation_errors)

        if not validation_errors:
            break

    state.stage4_output = output
    save_json(state.run_dir, "pipeline_state", state)
    return state
