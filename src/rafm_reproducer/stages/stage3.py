"""
Stage 3 — MECHANICAL: extract verbatim formula contents from the Low JSON.

For each numero in the approved Stage 2 selection, look up the node in the
Low JSON and attach its contenu (C-like source code).
Raises RuntimeError if a numero is not found in the Low JSON.
"""

from ..artifacts import save_json
from ..schemas.hierarchy import load_hierarchy
from ..schemas.pipeline_state import EnrichedFormula, PipelineState


def run_stage3(state: PipelineState) -> PipelineState:
    assert state.stage2_output is not None, "Stage 2 must complete before Stage 3"
    assert state.checkpoint2 is not None and state.checkpoint2.action == "approve"

    low_nodes = load_hierarchy(state.low_json_path)
    low_index = {n.numero: n for n in low_nodes}

    enriched: list[EnrichedFormula] = []
    for formula in state.stage2_output.selected:
        node = low_index.get(formula.numero)
        if node is None:
            raise RuntimeError(
                f"Stage 3: [{formula.numero}] '{formula.titre}' not found in Low JSON. "
                "Cannot proceed."
            )
        enriched.append(
            EnrichedFormula(
                numero=formula.numero,
                titre=formula.titre,
                role_one_line=formula.role_one_line,
                contenu=node.contenu or "",
            )
        )

    state.stage3_enriched = enriched
    save_json(state.run_dir, "stage3_enriched", [e.model_dump() for e in enriched])
    save_json(state.run_dir, "pipeline_state", state)
    return state
