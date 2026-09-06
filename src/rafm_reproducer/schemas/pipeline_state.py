from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .user_input import UserPrompt
from .stage1 import Stage1Output
from .stage2 import Stage2Output
from .stage4 import Stage4Output
from .stage5 import Stage5Output
from .stage6 import Stage6Output


class CheckpointAction(BaseModel):
    action: Literal["approve", "correct"]
    correction: str | None = None
    timestamp: str


class EnrichedFormula(BaseModel):
    """A selected formula with its verbatim contenu attached (output of Stage 3)."""
    numero: str
    titre: str
    role_one_line: str
    contenu: str


class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    run_dir: Path
    high_json_path: Path
    low_json_path: Path
    model_name: str

    user_prompt: UserPrompt | None = None

    # Stage 1
    stage1_output: Stage1Output | None = None
    stage1_history: list[Stage1Output] = []
    stage1_retry_count: int = 0
    checkpoint1: CheckpointAction | None = None

    # Stage 2
    stage2_output: Stage2Output | None = None
    stage2_history: list[Stage2Output] = []
    stage2_retry_count: int = 0
    # Validation warnings surfaced to UI without blocking
    stage2_validation_warnings: list[str] = []
    checkpoint2: CheckpointAction | None = None

    # Stage 3 (mechanical)
    stage3_enriched: list[EnrichedFormula] = []

    # Stages 4-7
    stage4_output: Stage4Output | None = None
    stage5_output: Stage5Output | None = None
    stage6_output: Stage6Output | None = None
    output_xlsx_path: Path | None = None
