"""
Stage 6 — LLM: self-review — compare the Excel spec against source code
and list omissions/simplifications.
"""

import json

from ..artifacts import save_json, save_text
from ..config import load_prompt
from ..llm_client import call_llm
from ..schemas.pipeline_state import PipelineState
from ..schemas.stage6 import Stage6Output
from ..validators.stage6 import validate_stage6

MAX_SEMANTIC_RETRIES = 2


def _build_user_prompt(state: PipelineState, validation_errors: list[str]) -> str:
    s5 = state.stage5_output
    enriched = state.stage3_enriched

    parts = [
        "=== EXCEL SPECIFICATION TO AUDIT ===",
        json.dumps(s5.model_dump(), indent=2, ensure_ascii=False) if s5 else "{}",
        "",
        "=== SOURCE FORMULAS WITH CODE ===",
    ]
    for ef in enriched:
        parts.append(f"\n--- [{ef.numero}] {ef.titre} ---")
        parts.append(ef.contenu if ef.contenu else "(no code)")

    if validation_errors:
        parts.append("\n\n=== VALIDATION ERRORS IN YOUR PREVIOUS OUTPUT — PLEASE FIX ===")
        for err in validation_errors:
            parts.append(f"- {err}")

    return "\n".join(parts)


def run_stage6(state: PipelineState, client, cfg: dict) -> PipelineState:
    assert state.stage5_output is not None, "Stage 5 must complete before Stage 6"

    system = load_prompt("stage6_system")
    validation_errors: list[str] = []
    output: Stage6Output | None = None

    for attempt in range(MAX_SEMANTIC_RETRIES + 1):
        label = f"stage6_attempt{attempt + 1}"
        user = _build_user_prompt(state, validation_errors)

        save_text(state.run_dir, f"{label}_prompt_system", system)
        save_text(state.run_dir, f"{label}_prompt_user", user)

        output = call_llm(
            client=client,
            model=cfg["model"],
            system=system,
            user=user,
            response_model=Stage6Output,
            effort=cfg.get("effort"),
            max_tokens=cfg.get("max_tokens"),
        )

        save_json(state.run_dir, f"{label}_output", output)

        validation_errors = validate_stage6(output, state.stage3_enriched)
        save_json(state.run_dir, f"{label}_validation_errors", validation_errors)

        if not validation_errors:
            break

    state.stage6_output = output
    save_json(state.run_dir, "pipeline_state", state)
    return state
