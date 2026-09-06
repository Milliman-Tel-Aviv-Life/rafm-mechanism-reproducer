import json
from datetime import datetime

from ..artifacts import save_json, save_text
from ..config import load_prompt
from ..llm_client import call_llm
from ..schemas.pipeline_state import CheckpointAction, PipelineState
from ..schemas.stage1 import Stage1Output
from ..validators.stage1 import validate_stage1

MAX_SEMANTIC_RETRIES = 2  # attempts after the initial call (3 total)


def _build_user_prompt(state: PipelineState, validation_errors: list[str]) -> str:
    """Compose the Stage 1 user prompt, injecting correction/history/errors as needed."""
    p = state.user_prompt
    base = load_prompt("stage1_user", text=p.text)
    parts = [base]

    # Re-run after Checkpoint 1 correction
    if state.checkpoint1 and state.checkpoint1.action == "correct":
        parts.append("\n\n=== USER CORRECTION ===")
        parts.append(state.checkpoint1.correction or "")
        if state.stage1_output:
            parts.append("\n\n=== YOUR PREVIOUS OUTPUT (revise this) ===")
            parts.append(state.stage1_output.model_dump_json(indent=2))
        if state.stage1_history:
            parts.append("\n\n=== HISTORY OF PREVIOUS ATTEMPTS ===")
            for i, h in enumerate(state.stage1_history, 1):
                parts.append(f"Attempt {i}:\n{h.model_dump_json(indent=2)}")

    # Validation-error retry
    if validation_errors:
        parts.append("\n\n=== VALIDATION ERRORS IN YOUR PREVIOUS OUTPUT — PLEASE FIX ===")
        for err in validation_errors:
            parts.append(f"- {err}")

    return "\n".join(parts)


def run_stage1(state: PipelineState, client, cfg: dict) -> PipelineState:
    """
    Run Stage 1: LLM interprets the user request.
    Handles up to MAX_SEMANTIC_RETRIES additional attempts on validation failure.
    Saves all prompts, raw outputs, and validation errors to run_dir.
    """
    system = load_prompt("stage1_system")
    validation_errors: list[str] = []
    output: Stage1Output | None = None

    for attempt in range(MAX_SEMANTIC_RETRIES + 1):
        label = f"stage1_attempt{attempt + 1}"
        user = _build_user_prompt(state, validation_errors)

        save_text(state.run_dir, f"{label}_prompt_system", system)
        save_text(state.run_dir, f"{label}_prompt_user", user)

        output = call_llm(
            client=client,
            model=cfg["model"],
            system=system,
            user=user,
            response_model=Stage1Output,
            effort=cfg.get("effort"),
            max_tokens=cfg.get("max_tokens"),
        )

        save_json(state.run_dir, f"{label}_output", output)

        if output.halt_reason:
            # Halt is a valid terminal state — surface to UI, stop retrying.
            break

        validation_errors = validate_stage1(output)
        save_json(state.run_dir, f"{label}_validation_errors", validation_errors)

        if not validation_errors:
            break
        # else: loop continues with errors injected into the next prompt

    # Archive previous output in history before replacing
    if state.stage1_output is not None:
        state.stage1_history.append(state.stage1_output)

    state.stage1_output = output
    state.stage1_retry_count = 0  # reset for next user-triggered re-run
    save_json(state.run_dir, "pipeline_state", state)
    return state
