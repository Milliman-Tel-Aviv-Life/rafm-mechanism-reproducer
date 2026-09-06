import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = _ROOT / "config.yaml"
PROMPTS_DIR = Path(__file__).parent / "prompts"
RUNS_DIR = _ROOT / "runs"


def _read_raw() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def provider_for(model: str) -> str:
    """Which APIM route a model speaks. claude-* use the native Anthropic API."""
    return "anthropic" if model.lower().startswith("claude") else "azure_openai"


# A stage that blows past its budget is a bug, not a slow answer: the call is cut
# instead of hanging. Anything not given a budget falls back to this.
DEFAULT_TIMEOUT_S = 300
DEFAULT_MAX_TOKENS = 16000


def _build_cfg(
    raw: dict,
    model: str,
    effort: str | None = None,
    timeout_s: int | None = None,
    max_tokens: int | None = None,
) -> dict:
    endpoint = os.getenv("RAFM_ENDPOINT") or raw.get("endpoints", {}).get("tel_aviv", "")
    api_key = os.getenv("RAFM_API_KEY") or raw.get("api_keys", {}).get("tel_aviv", "")
    api_version = raw.get("llm_api_versions", {}).get(model) or "2024-12-01-preview"
    return {
        "model": model,
        "provider": provider_for(model),
        "endpoint": endpoint,
        "api_key": api_key,
        "api_version": api_version,
        "effort": effort,
        "timeout_s": timeout_s or DEFAULT_TIMEOUT_S,
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
    }


def _stage_entry(raw: dict, stage: str) -> dict:
    """Unpack a stage_models entry, which may be a bare model name or a dict."""
    entry = raw.get("stage_models", {}).get(stage)
    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, str):
        return {"model": entry}
    return {}


def load_config() -> dict:
    """Default config — used for the sidebar default and as a fallback."""
    raw = _read_raw()
    model = os.getenv("RAFM_MODEL") or raw.get("llm_deployment", "claude-sonnet-5")
    return _build_cfg(raw, model)


def load_premium_cfg() -> dict:
    """Config for the premium model used by the heavy reasoning stages."""
    raw = _read_raw()
    model = raw.get("llm_deployment_premium") or raw.get("llm_deployment", "claude-opus-4-8")
    return _build_cfg(raw, model)


def load_stage_cfg(stage: str, override: str | None = None) -> dict:
    """
    Config for one pipeline stage (e.g. "stage4").

    Resolution order, first hit wins:
      override (forced from the UI) → RAFM_MODEL_<STAGE> env →
      stage_models[stage] in config.yaml → RAFM_MODEL env → llm_deployment.

    A configured effort still applies under an override — it describes how hard
    the stage is, not which model runs it.
    """
    raw = _read_raw()
    entry = _stage_entry(raw, stage)
    model = (
        override
        or os.getenv(f"RAFM_MODEL_{stage.upper()}")
        or entry.get("model")
        or os.getenv("RAFM_MODEL")
        or raw.get("llm_deployment", "claude-sonnet-5")
    )
    return _build_cfg(
        raw, model, entry.get("effort"), entry.get("timeout_s"), entry.get("max_tokens")
    )


DOCS_DIR = _ROOT / "docs"


def available_clients() -> dict[str, tuple[Path, Path]]:
    """
    {client: (high_path, low_path)} for every complete pair found in docs/.

    Discovered from the filenames the extraction script writes, so converting a
    new audit report is enough to make that client selectable — no code change.
    A High without its Low (or the reverse) is skipped: both are needed, Stage 2
    to select and Stage 3 to fetch the code.
    """
    found: dict[str, tuple[Path, Path]] = {}
    for high in sorted(DOCS_DIR.glob("Hierarchy_*_High.json")):
        client = high.name[len("Hierarchy_"):-len("_High.json")]
        low = DOCS_DIR / f"hierarchie_{client.lower()}_Low.json"
        if low.exists():
            found[client] = (high, low)
    return found


def load_prompt(name: str, **kwargs: str) -> str:
    """Load a prompt .txt file and fill {placeholders} with kwargs."""
    path = PROMPTS_DIR / f"{name}.txt"
    text = path.read_text(encoding="utf-8")
    if kwargs:
        text = text.format_map(kwargs)
    return text


def available_models() -> list[str]:
    return list(_read_raw().get("llm_api_versions", {}).keys())


def stage_model_map() -> dict[str, str]:
    """{stage: model} for every stage listed in config.yaml — for display."""
    raw = _read_raw()
    return {
        stage: _stage_entry(raw, stage).get("model")
        for stage in raw.get("stage_models", {})
    }
