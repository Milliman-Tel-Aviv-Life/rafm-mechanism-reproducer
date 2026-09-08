from ..schemas.stage5 import Stage5Output
from ..schemas.stage4 import Stage4Output


def validate_stage5(output: Stage5Output, stage4: Stage4Output) -> list[str]:
    errors: list[str] = []
    stage4_numeros = {fd.numero for fd in stage4.formulas}
    input_names = {inp.name for inp in output.inputs}

    # Source citations
    for calc in output.calculations:
        if calc.source_formula_number not in stage4_numeros:
            errors.append(
                f"Calculation '{calc.column_name}' cites source formula "
                f"[{calc.source_formula_number}] which does not exist in Stage 4 output."
            )

    # Topological order: each name in depends_on must appear earlier
    seen_columns: set[str] = set()
    for calc in output.calculations:
        for dep in calc.depends_on:
            # A temporal column reads its own previous-period value — that is
            # what makes it temporal, not a forward reference.
            if dep == calc.column_name and calc.kind == "temporal":
                continue
            if dep not in seen_columns and dep not in input_names:
                errors.append(
                    f"Calculation '{calc.column_name}' depends on '{dep}' "
                    f"which has not been declared yet (forward reference or missing input)."
                )
        seen_columns.add(calc.column_name)

    # Scenario length
    if output.scenario.n_periods != len(output.scenario.default_values):
        errors.append(
            f"scenario.n_periods={output.scenario.n_periods} but "
            f"len(scenario.default_values)={len(output.scenario.default_values)}."
        )

    # Driving input exists
    if output.scenario.driving_input not in input_names:
        errors.append(
            f"scenario.driving_input='{output.scenario.driving_input}' "
            f"is not declared in inputs."
        )

    return errors
