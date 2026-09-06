from ..artifacts import save_json, save_text
from ..config import load_prompt
from ..llm_client import call_llm
from ..schemas.hierarchy import (
    HierarchyNode,
    build_index,
    load_hierarchy,
    load_model_tree,
    to_compact_text,
)
from ..schemas.pipeline_state import PipelineState
from ..schemas.stage2 import Stage2Output
from ..validators.stage2 import validate_stage2

MAX_SEMANTIC_RETRIES = 2


def _build_user_prompt(
    state: PipelineState,
    hierarchy_text: str,
    validation_errors: list[str],
) -> str:
    s1 = state.stage1_output
    base = load_prompt(
        "stage2_user",
        interpretation=s1.interpretation,
        relevant_branches="\n".join(f"- {b}" for b in s1.relevant_branches),
        keyword_hints="\n".join(f"- {k}" for k in s1.keyword_hints),
        hierarchy=hierarchy_text,
    )
    parts = [base]

    # Re-run after Checkpoint 2 correction
    if state.checkpoint2 and state.checkpoint2.action == "correct":
        parts.append("\n\n=== USER CORRECTION ===")
        parts.append(state.checkpoint2.correction or "")
        if state.stage2_output:
            parts.append("\n\n=== YOUR PREVIOUS OUTPUT (revise this) ===")
            parts.append(state.stage2_output.model_dump_json(indent=2))
        if state.stage2_history:
            parts.append("\n\n=== HISTORY OF PREVIOUS ATTEMPTS ===")
            for i, h in enumerate(state.stage2_history, 1):
                parts.append(f"Attempt {i}:\n{h.model_dump_json(indent=2)}")

    # Validation-error retry
    if validation_errors:
        parts.append("\n\n=== VALIDATION ERRORS IN YOUR PREVIOUS OUTPUT — PLEASE FIX ===")
        for err in validation_errors:
            parts.append(f"- {err}")

    return "\n".join(parts)


def run_stage2(state: PipelineState, client, cfg: dict) -> PipelineState:
    """
    Run Stage 2: LLM selects defining formulas from the full High JSON.
    Saves all prompts, raw outputs, and validation errors to run_dir.
    """
    assert state.stage1_output is not None, "Stage 1 must complete before Stage 2"
    assert state.checkpoint1 is not None and state.checkpoint1.action == "approve", (
        "Checkpoint 1 must be approved before Stage 2"
    )

    nodes: list[HierarchyNode] = load_hierarchy(state.high_json_path)
    index = build_index(nodes)
    # The model map lives in the Low JSON; read once, before the retry loop.
    hierarchy_text = to_compact_text(nodes, load_model_tree(state.low_json_path))

    system = load_prompt("stage2_system")
    validation_errors: list[str] = []
    output: Stage2Output | None = None
    final_warnings: list[str] = []

    for attempt in range(MAX_SEMANTIC_RETRIES + 1):
        label = f"stage2_attempt{attempt + 1}"
        user = _build_user_prompt(state, hierarchy_text, validation_errors)

        save_text(state.run_dir, f"{label}_prompt_system", system)
        save_text(state.run_dir, f"{label}_prompt_user", user)

        output = call_llm(
            client=client,
            model=cfg["model"],
            system=system,
            user=user,
            response_model=Stage2Output,
            effort=cfg.get("effort"),
            max_tokens=cfg.get("max_tokens"),
        )

        save_json(state.run_dir, f"{label}_output", output)

        validation_errors = validate_stage2(output, index)
        save_json(state.run_dir, f"{label}_validation_errors", validation_errors)

        if not validation_errors:
            break

        if attempt == MAX_SEMANTIC_RETRIES:
            # All retries exhausted — surface warnings to UI but don't block.
            final_warnings = validation_errors

    if state.stage2_output is not None:
        state.stage2_history.append(state.stage2_output)

    state.stage2_output = output
    state.stage2_validation_warnings = final_warnings
    state.stage2_retry_count = 0
    save_json(state.run_dir, "pipeline_state", state)
    return state
