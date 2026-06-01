# app/services/analysis_service.py
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from jsonschema import validate, ValidationError
from openai import OpenAI, APITimeoutError, APIStatusError, RateLimitError

from app.core.config import OPENAI_API_KEY
from app.core.logging_config import logger

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


# ---------------------- Prompt (schema-aligned) ----------------------
PROMPT_SYSTEM = (
    "You are a clinical evidence extractor. Produce ONE PICO(S) JSON object that MUST validate "
    "against the provided JSON Schema (exact keys; additionalProperties=false).\n"
    "Rules:\n"
    "1) Use ONLY information explicitly present in the text. No inference.\n"
    "2) Always include every REQUIRED key from the schema.\n"
    "3) Missing values:\n"
    "   • For fields whose type allows null (e.g., [\"string\",\"null\"], [\"array\",\"null\"], objects with null): use null.\n"
    "   • For fields that are strictly string (\"type\":\"string\") and do NOT allow null: use an empty string \"\".\n"
    "   • For arrays: if nothing is reported, prefer [] (unless the schema for that array explicitly allows null and you choose to use null).\n"
    "4) Keep original wording/units (mg, mg/kg, q8w, SC/IV, etc.).\n"
    "5) Prefer Methods/Results over Introduction/Discussion; ignore affiliations, references, figure/table captions, headers/footers.\n"
    "6) If multiple options exist, choose the primary cohort/arm/timepoint of the main analysis; otherwise leave the field missing per rule 3.\n"
    "7) Output JSON ONLY. No prose. No extra keys. No multiple objects. No trailing commas."
)

PROMPT_USER_TEMPLATE = (
    "Extract PICO(S) strictly following the rules and the JSON Schema constraints.\n\n"
    "TEXT START\n{body}\nTEXT END"
)




def _build_messages(entry: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": PROMPT_SYSTEM},
        {"role": "user", "content": PROMPT_USER_TEMPLATE.format(body=entry)},
    ]


# ---------------------- Utils ----------------------
def _extract_usage(resp: Any) -> Dict[str, int | None]:
    """
    Best-effort extraction of token usage from different SDK shapes.
    """
    usage: Dict[str, int | None] = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return usage
        if isinstance(u, dict):
            usage["input_tokens"] = u.get("input_tokens") or u.get("prompt_tokens")
            usage["output_tokens"] = u.get("output_tokens") or u.get("completion_tokens")
            usage["total_tokens"] = u.get("total_tokens")
            return usage
        usage["input_tokens"] = getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", None)
        usage["output_tokens"] = getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", None)
        usage["total_tokens"] = getattr(u, "total_tokens", None)
    except Exception:
        pass
    return usage


def _coerce_to_dict(raw: Any) -> Dict[str, Any]:
    """
    Accepts a dict, or an object with .text JSON, or a JSON string.
    Returns a dict ready for jsonschema validation.
    """
    if isinstance(raw, dict):
        return raw
    text = getattr(raw, "text", None)
    if isinstance(text, str):
        return json.loads(text)
    if isinstance(raw, str):
        return json.loads(raw)
    # Last resort (will raise if invalid)
    return json.loads(str(raw))


def _call_openai(
    *,
    model: str,
    messages: List[Dict[str, str]],
    schema: Dict[str, Any],
    temperature: float,
    top_p: float,
) -> Tuple[Any, Dict[str, int | None]]:
    """Call OpenAI with retries. Returns (structured_payload, usage_tokens)."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment or to .env at the project root."
        )

    client = OpenAI(api_key=OPENAI_API_KEY)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = client.responses.create(
                model=model,
                input=messages,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "PICO_response",
                        "schema": schema,
                        "strict": True,
                    }
                },
                temperature=temperature,
                top_p=top_p,
            )
            payload = resp.output[0].content[0]  # structured output blob
            usage = _extract_usage(resp)
            return payload, usage

        except (RateLimitError, APITimeoutError, APIStatusError) as err:
            if attempt == _MAX_RETRIES:
                logger.error("OpenAI call failed after %s attempts", attempt, exc_info=err)
                raise
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "%s (attempt %s/%s) – retrying in %ss",
                type(err).__name__,
                attempt,
                _MAX_RETRIES,
                delay,
            )
            time.sleep(delay)


# ---------------------- Public API ----------------------
def analyze_entry_with_openai(
    entry: str,
    *,
    model: str = "gpt-4o",
    temperature: float = 0.1,
    top_p: float = 0.1,
    schema_override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Returns ONLY the PICO dict. Requires schema_override (no internal fallback).
    """
    if schema_override is None:
        raise ValueError("schema_override is required (no internal fallback schema).")

    messages = _build_messages(entry)
    logger.info("Calling OpenAI (abstract length: %s chars)", len(entry))

    raw, _ = _call_openai(
        model=model,
        messages=messages,
        schema=schema_override,
        temperature=temperature,
        top_p=top_p,
    )
    data = _coerce_to_dict(raw)

    try:
        validate(instance=data, schema=schema_override)
    except ValidationError as ve:
        logger.error("Schema validation failed: %s", ve.message)
        raise

    logger.info("PICO extraction successful")
    return data


def analyze_entry_with_openai_with_usage(
    entry: str,
    *,
    model: str = "gpt-4o",
    temperature: float = 0.1,
    top_p: float = 0.1,
    schema_override: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, int | None]]:
    """
    Returns (pico_dict, usage_tokens). Requires schema_override (no internal fallback).
    """
    if schema_override is None:
        raise ValueError("schema_override is required (no internal fallback schema).")

    messages = _build_messages(entry)
    logger.info("Calling OpenAI (abstract length: %s chars)", len(entry))

    raw, usage = _call_openai(
        model=model,
        messages=messages,
        schema=schema_override,
        temperature=temperature,
        top_p=top_p,
    )
    data = _coerce_to_dict(raw)

    try:
        validate(instance=data, schema=schema_override)
    except ValidationError as ve:
        logger.error("Schema validation failed: %s", ve.message)
        raise

    logger.info("PICO extraction successful")
    return data, usage
