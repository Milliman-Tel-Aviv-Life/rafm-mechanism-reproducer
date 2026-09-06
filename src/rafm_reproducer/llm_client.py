import time
from typing import TypeVar, Type

import anthropic
import httpx
import instructor
from openai import AzureOpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# The APIM exposes the native Anthropic Messages API under this path prefix,
# so the SDK's own /v1/messages suffix lands on the right route.
_ANTHROPIC_ROUTE = "/anthropic"
_DEFAULT_MAX_TOKENS = 16000
_DEFAULT_TIMEOUT_S = 300.0
# Each schema retry costs another full generation, so on a reasoning stage a
# generous count multiplies the stage's wall-clock instead of saving it.
_DEFAULT_SCHEMA_RETRIES = 2
_ANSWER_TOOL = "final_answer"
# Extra attempts reserved for the gateway's intermittent 400s, on top of
# the schema retries — a transport hiccup should not cost a stage.
_GATEWAY_RETRIES = 2
_GATEWAY_BACKOFF_S = 3.0


def is_anthropic(model: str) -> bool:
    return model.lower().startswith("claude")


def get_client(cfg: dict):
    """
    Build the client for the route the model speaks.

    claude-* get the raw Anthropic SDK client — the structured-output loop below
    drives it directly, because it has to stream (see `_call_anthropic`). Azure
    OpenAI keeps the instructor wrapper.
    """
    timeout = float(cfg.get("timeout_s") or _DEFAULT_TIMEOUT_S)

    if cfg.get("provider") == "anthropic" or is_anthropic(cfg["model"]):
        return anthropic.Anthropic(
            # The APIM authenticates on its own api-key header, but the SDK
            # refuses to start without api_key set — hence the placeholder.
            api_key="unused",
            base_url=cfg["endpoint"].rstrip("/") + _ANTHROPIC_ROUTE,
            default_headers={"api-key": cfg["api_key"]},
            # Idle timeout, not a total budget: the anthropic path streams, so
            # this fires only when the gateway goes quiet for that long. Total
            # wall-clock is bounded by max_tokens and effort instead.
            timeout=timeout,
            max_retries=0,
        )

    # No connection pooling — each call gets a fresh TCP connection.
    # This prevents stale-connection errors when one stage finishes and
    # the next stage immediately starts a new request to the APIM.
    http_client = httpx.Client(
        timeout=httpx.Timeout(timeout),
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
    )
    azure_client = AzureOpenAI(
        api_key=cfg["api_key"],
        azure_endpoint=cfg["endpoint"],
        api_version=cfg.get("api_version") or "2024-12-01-preview",
        http_client=http_client,
    )
    return instructor.from_openai(azure_client, mode=instructor.Mode.JSON)


def _closed_schema(schema: dict) -> dict:
    """
    Close every object in a JSON schema (`additionalProperties: false`), which
    strict tool use requires. Pydantic does not emit it, and it has to reach the
    nested models under `$defs` too, not just the top level.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            schema["additionalProperties"] = False
        for value in schema.values():
            _closed_schema(value)
    elif isinstance(schema, list):
        for item in schema:
            _closed_schema(item)
    return schema


def _call_anthropic(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    response_model: Type[T],
    max_retries: int,
    effort: str | None,
    max_tokens: int,
) -> T:
    """
    Structured output over a streamed Anthropic call.

    Streaming is not an optimisation here, it is the only thing that works: the
    APIM kills a long non-streamed request, first as a read timeout and then as
    HTTP 500 "Internal gateway error". The same prompt streams fine. instructor
    cannot cover this — its create() is non-streamed, and create_partial() yields
    nothing in tool mode — so the tool round-trip is done by hand.

    tool_choice stays "auto": forcing the tool silently disables extended
    thinking (0 reasoning tokens and visibly worse answers), so the tool is
    requested in the prompt instead.
    """
    tool = {
        "name": _ANSWER_TOOL,
        "description": f"Return the final answer as a {response_model.__name__} object.",
        "input_schema": _closed_schema(response_model.model_json_schema()),
        # Without this the model happily returns a partial object — on Stage 4 it
        # sent `reasoning` alone and dropped `formulas`, well under max_tokens.
        # strict makes the API guarantee the input matches the schema.
        "strict": True,
    }
    instruction = (
        f"\n\nReturn your answer by calling the `{_ANSWER_TOOL}` tool exactly once, "
        "with every field filled in. Do not answer in plain text."
    )
    messages: list[dict] = [{"role": "user", "content": user}]
    extra: dict = {"output_config": {"effort": effort}} if effort else {}

    last_error: Exception | None = None
    for attempt in range(max(1, max_retries) + _GATEWAY_RETRIES):
        try:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                # Adaptive thinking: the model spends reasoning tokens only on
                # the calls that need them, so cheap stages stay cheap.
                thinking={"type": "adaptive"},
                system=system + instruction,
                messages=messages,
                tools=[tool],
                **extra,
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIStatusError as e:
            # The APIM intermittently rejects a request it accepts on the next
            # try, with a bare 400 "Invalid request data". The SDK does not retry
            # 400s, and the identical payload succeeds moments later, so retry
            # here rather than failing a whole stage on gateway noise.
            last_error = e
            if attempt + 1 < max(1, max_retries) + _GATEWAY_RETRIES:
                time.sleep(_GATEWAY_BACKOFF_S * (attempt + 1))
                continue
            raise

        # Truncation must be caught before validation: a run cut at max_tokens
        # still yields a tool_use block, just an incomplete one, and the missing
        # fields then look like the model disobeying rather than running out of
        # room. Retrying that is pure waste — it will truncate again.
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                f"{model} hit max_tokens ({max_tokens}) while writing "
                f"{response_model.__name__} (thinking+output truncated) — raise "
                "max_tokens for this stage or lighten its schema."
            )

        block = next(
            (b for b in message.content if b.type == "tool_use" and b.name == _ANSWER_TOOL),
            None,
        )
        if block is None:
            last_error = RuntimeError(
                f"{model} returned no {_ANSWER_TOOL} call (stop_reason="
                f"{message.stop_reason})"
            )
        else:
            try:
                return response_model.model_validate(block.input)
            except ValidationError as e:
                last_error = e

        # Feed the failure back so the retry fixes it instead of repeating it.
        messages = [
            {"role": "user", "content": user},
            {"role": "user", "content": f"Your previous answer was invalid: {last_error}"},
        ]

    raise last_error or RuntimeError("structured call failed")


def call_llm(
    client,
    model: str,
    system: str,
    user: str,
    response_model: Type[T],
    max_retries: int = _DEFAULT_SCHEMA_RETRIES,
    effort: str | None = None,
    max_tokens: int | None = None,
) -> T:
    """
    Call the LLM and return a validated Pydantic object.
    Schema-level retries happen here (up to max_retries). Semantic-level retries
    (numero existence checks, etc.) are handled by callers.

    `effort` is honoured on claude-* models only; the Azure route ignores it.
    `max_tokens` caps generation, and generation length is what drives a stage's
    wall-clock — it is the lever that keeps a stage inside its time budget.
    """
    max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
    if is_anthropic(model):
        return _call_anthropic(
            client, model, system, user, response_model, max_retries, effort, max_tokens
        )

    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_model=response_model,
        max_retries=max_retries,
    )
