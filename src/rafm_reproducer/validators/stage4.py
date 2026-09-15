from ..schemas.stage4 import Stage4Output
from ..schemas.pipeline_state import EnrichedFormula


def validate_stage4(output: Stage4Output, enriched: list[EnrichedFormula]) -> list[str]:
    errors: list[str] = []
    enriched_numeros = {f.numero for f in enriched}

    # An empty or partial decomposition is schema-valid but leaves Stage 5
    # nothing to cite, and it then reconstructs the mechanism from names alone.
    if not output.formulas:
        errors.append(
            "formulas is empty — every selected formula must be decomposed "
            "(inputs_used, branches, temporal_self_reference); the analysis must "
            "not stay in `reasoning` only."
        )
    else:
        decomposed = {fd.numero for fd in output.formulas}
        for f in enriched:
            if f.numero not in decomposed:
                errors.append(
                    f"Selected formula [{f.numero}] {f.titre} has no entry in formulas."
                )

    for fd in output.formulas:
        if fd.numero not in enriched_numeros:
            errors.append(
                f"Stage 4 references numero [{fd.numero}] which is not in the Stage 3 "
                f"enriched selection."
            )

    for ssx in output.shared_subexpressions:
        for ref in ssx.appears_in:
            if not any(fd.numero == ref for fd in output.formulas):
                errors.append(
                    f"shared_subexpressions '{ssx.name}' references [{ref}] "
                    f"which is not in the formulas list."
                )

    for ki in output.key_identities:
        for ref in ki.source_formulas:
            if not any(fd.numero == ref for fd in output.formulas):
                errors.append(
                    f"key_identity '{ki.identity}' references [{ref}] "
                    f"which is not in the formulas list."
                )

    return errors
