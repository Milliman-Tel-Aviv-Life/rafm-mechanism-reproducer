from typing import Literal

from pydantic import BaseModel


class InputSpec(BaseModel):
    name: str
    label: str
    default_value: float | None
    format: str
    comment: str


class ScenarioSpec(BaseModel):
    driving_input: str
    n_periods: int
    default_values: list[float]


class CalculationSpec(BaseModel):
    column_name: str
    label: str
    kind: Literal["always_computed", "conditional", "temporal"]
    formula_excel: str
    condition_excel: str | None
    format: str
    depends_on: list[str]
    source_formula_number: str
    source_excerpt: str


class ColorRule(BaseModel):
    rule_excel: str
    background_hex: str
    label: str


class Note(BaseModel):
    section: str
    text: str


class KeyRelation(BaseModel):
    identity: str
    source_formulas: list[str]


class Stage5Output(BaseModel):
    reasoning: str          # step-by-step explanation of design choices before finalising the spec
    mechanism_name: str
    scope_description: str
    key_relation: KeyRelation
    inputs: list[InputSpec]
    scenario: ScenarioSpec
    calculations: list[CalculationSpec]
    color_rules: list[ColorRule]
    notes: list[Note]
