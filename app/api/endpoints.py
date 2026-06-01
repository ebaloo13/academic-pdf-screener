# app/api/endpoints.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.core.schema_loader import load_required_json_schema
from app.services.analysis_service import analyze_entry_with_openai
from app.utils.pdf_reader import extract_text_from_pdf

router = APIRouter()

# -------------------- Request / Response Models --------------------

class AnalyzeTextRequest(BaseModel):
    text: str = Field(..., description="Raw abstract or article text to analyze.")
    schema_path: str = Field(..., description="Path to JSON schema, e.g., 'config/schemas/pico_v1.json'.")
    persist: bool = Field(False, description="If true, save input/output under reports/")
    run_id: Optional[str] = Field(None, description="Optional custom RUN_ID for traceability.")
    # model params (optional, KISS defaults)
    temperature: float = Field(0.1, ge=0.0, le=2.0, description="Sampling temperature.")
    top_p: float = Field(0.1, ge=0.0, le=1.0, description="Nucleus sampling.")

class AnalyzePdfRequest(BaseModel):
    pdf_path: str = Field(..., description="Path to a PDF (e.g., 'data/pdf/UC1597_Danese_2022.pdf').")
    # slice options
    start_kw: Optional[str] = Field("methods", description="Slice start keyword; use null for full text (w/ end_kw null).")
    end_kw: Optional[str] = Field("discussion", description="Slice end keyword; use null for full text (w/ start_kw null).")
    start_occurrence: int = Field(1, ge=1, description="1 = first match, 2 = second occurrence (skip front-page block).")
    use_outlines: bool = Field(True, description="Use PDF bookmarks to refine slice if available.")
    remove_headers: bool = Field(True, description="Strip repeated headers/footers lines.")
    normalize_ligatures: bool = Field(True, description="Normalize ligatures (ﬁ → fi).")
    dehyphen: bool = Field(True, description="Join words split by hyphen at line breaks.")
    enforce_two_columns: bool = Field(False, description="Force two-column reading even if not detected.")
    # schema + io
    schema_path: str = Field(..., description="Path to JSON schema, e.g., 'config/schemas/pico_v1.json'.")
    persist: bool = Field(False, description="If true, save input/output under reports/")
    run_id: Optional[str] = Field(None, description="Optional custom RUN_ID for traceability.")
    # model params (optional)
    temperature: float = Field(0.1, ge=0.0, le=2.0, description="Sampling temperature.")
    top_p: float = Field(0.1, ge=0.0, le=1.0, description="Nucleus sampling.")

class AnalyzeResponse(BaseModel):
    run_id: str
    pico: Dict[str, Any]

# -------------------- Helpers --------------------

def _ensure_parent_dirs() -> None:
    Path("reports/inputs").mkdir(parents=True, exist_ok=True)
    Path("reports/examples").mkdir(parents=True, exist_ok=True)

def _make_run_id(prefix: str = "api") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# -------------------- Endpoints --------------------

@router.post("/analyze-text", response_model=AnalyzeResponse)
def analyze_text(req: AnalyzeTextRequest) -> AnalyzeResponse:
    schema, schema_sha = load_required_json_schema(req.schema_path)

    run_id = req.run_id or _make_run_id()
    header = (
        f"RUN_ID={run_id} | INPUT=text | SCHEMA={Path(req.schema_path).name} | "
        f"SCHEMA_SHA256={schema_sha[:12]}\n\n"
    )
    payload = header + req.text

    if req.persist:
        _ensure_parent_dirs()
        base = f"text_{run_id}"
        (Path("reports/inputs") / f"{base}.txt").write_text(payload, encoding="utf-8")

    try:
        pico = analyze_entry_with_openai(
            payload,
            schema_override=schema,          # <- FIX kwarg
            temperature=req.temperature,
            top_p=req.top_p,
        )
    except Exception as e:
        logger.exception("Failed analyze_text RUN_ID=%s", run_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if req.persist:
        base = f"text_{run_id}"
        (Path("reports/examples") / f"{base}.json").write_text(
            json.dumps(pico, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8"
        )

    return AnalyzeResponse(run_id=run_id, pico=pico)

@router.post("/analyze-pdf", response_model=AnalyzeResponse)
def analyze_pdf(req: AnalyzePdfRequest) -> AnalyzeResponse:
    schema, schema_sha = load_required_json_schema(req.schema_path)

    pdf_path = Path(req.pdf_path).resolve()
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")

    # If either is None => full text
    start_kw = None if (req.start_kw is None or req.end_kw is None) else req.start_kw
    end_kw = None if (req.start_kw is None or req.end_kw is None) else req.end_kw

    text = extract_text_from_pdf(
        pdf_path,
        start_kw=start_kw,
        end_kw=end_kw,
        use_outlines=req.use_outlines,
        remove_headers=req.remove_headers,
        normalize_ligatures=req.normalize_ligatures,
        dehyphen=req.dehyphen,
        enforce_two_columns=req.enforce_two_columns,
        start_occurrence=req.start_occurrence,   # <- NUEVO si tu pdf_reader lo soporta
    )
    if not text or len(text) < 200:
        raise HTTPException(status_code=400, detail="Text extraction returned too little content.")

    run_id = req.run_id or _make_run_id()
    slice_tag = ("full" if start_kw is None or end_kw is None else f"{start_kw}-{end_kw}").replace(" ", "_")

    header = (
        f"RUN_ID={run_id} | FILE={pdf_path.stem} | SLICE={slice_tag} | "
        f"SCHEMA={Path(req.schema_path).name} | SCHEMA_SHA256={schema_sha[:12]}\n\n"
    )
    payload = header + text

    if req.persist:
        _ensure_parent_dirs()
        base = f"{pdf_path.stem}_{slice_tag}_{run_id}"
        (Path("reports/inputs") / f"{base}.txt").write_text(payload, encoding="utf-8")

    try:
        pico = analyze_entry_with_openai(
            payload,
            schema_override=schema,          # <- FIX kwarg
            temperature=req.temperature,
            top_p=req.top_p,
        )
    except Exception as e:
        logger.exception("Failed analyze_pdf RUN_ID=%s", run_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if req.persist:
        base = f"{pdf_path.stem}_{slice_tag}_{run_id}"
        (Path("reports/examples") / f"{base}.json").write_text(
            json.dumps(pico, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8"
        )

    return AnalyzeResponse(run_id=run_id, pico=pico)
