"""
Stage 5 — LLM: produce the complete Excel JSON specification.
"""

import json

from ..artifacts import save_json, save_text
from ..config import load_prompt
from ..llm_client import call_llm
from ..schemas.pipeline_state import PipelineState
from ..schemas.stage5 import Stage5Output
from ..validators.stage5 import validate_stage5

MAX_SEMANTIC_RETRIES = 2


def _s4_compact(s4) -> dict:
    """Strip fields that are bulky and not needed by Stage 5."""
    if s4 is None:
        return {}
    d = s4.model_dump()
    d.pop("reasoning", None)          # narrative, not needed
    return d


def _build_user_prompt(state: PipelineState, validation_errors: list[str]) -> str:
    s4 = state.stage4_output

    parts = [
        "=== USER REQUEST ===",
        state.user_prompt.text if state.user_prompt else "",
        "",
        "=== MECHANISM ANALYSIS (Stage 4 output) ===",
        json.dumps(_s4_compact(s4), indent=2, ensure_ascii=False),
    ]

    if validation_errors:
        parts.append("\n\n=== VALIDATION ERRORS IN YOUR PREVIOUS OUTPUT — PLEASE FIX ===")
        for err in validation_errors:
            parts.append(f"- {err}")

    return "\n".join(parts)


def run_stage5(state: PipelineState, client, cfg: dict) -> PipelineState:
    assert state.stage4_output is not None, "Stage 4 must complete before Stage 5"

    system = load_prompt("stage5_system")
    validation_errors: list[str] = []
    output: Stage5Output | None = None

    for attempt in range(MAX_SEMANTIC_RETRIES + 1):
        label = f"stage5_attempt{attempt + 1}"
        user = _build_user_prompt(state, validation_errors)

        save_text(state.run_dir, f"{label}_prompt_system", system)
        save_text(state.run_dir, f"{label}_prompt_user", user)

        output = call_llm(
            client=client,
            model=cfg["model"],
            system=system,
            user=user,
            response_model=Stage5Output,
            effort=cfg.get("effort"),
            max_tokens=cfg.get("max_tokens"),
        )

        save_json(state.run_dir, f"{label}_output", output)

        validation_errors = validate_stage5(output, state.stage4_output)
        save_json(state.run_dir, f"{label}_validation_errors", validation_errors)

        if not validation_errors:
            break

    state.stage5_output = output
    save_json(state.run_dir, "pipeline_state", state)
    return state
