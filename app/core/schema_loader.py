# app/core/schema_loader.py
from __future__ import annotations

import json
import hashlib
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, Tuple

from jsonschema import Draft202012Validator
from app.core.logging_config import logger


def load_required_json_schema(path: str | Path) -> Tuple[Dict[str, Any], str]:
    """
    Load a JSON Schema from disk, validate the SCHEMA itself (draft 2020-12),
    and return (schema_dict, sha256_hex).
    - Hard-fails on any issue (no fallbacks).
    - Minimal and KISS by design.
    """
    p = Path(path).resolve()

    if p.suffix.lower() != ".json":
        raise ValueError(f"Schema must be a .json file: {p}")
    if not p.exists():
        # Explicit message you wanted:
        raise FileNotFoundError(f"Schema file not found: {p}")

    try:
        schema: Dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse JSON schema at {p} (line {e.lineno}, col {e.colno}): {e.msg}"
        ) from e
    except Exception as e:
        raise ValueError(f"Failed to read schema at {p}: {e}") from e

    # Meta-validation of the schema (removes doubts about structure)
    Draft202012Validator.check_schema(schema)

    # Stable hash for traceability in logs and outputs
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(canonical).hexdigest()

    logger.info("Loaded JSON Schema %s (sha256=%s)", p.name, sha[:12])
    return schema, sha
