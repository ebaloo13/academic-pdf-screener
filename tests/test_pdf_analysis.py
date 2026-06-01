# tests/test_pdf_analysis.py
import os
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from app.core.schema_loader import load_required_json_schema
from app.services.analysis_service import analyze_entry_with_openai

# Load schema from disk (mandatory – no fallback)
SCHEMA_PATH = Path(os.getenv("SCHEMA_PATH", "config/schemas/pico_v1.json"))
PICO_SCHEMA, _SCHEMA_SHA = load_required_json_schema(SCHEMA_PATH)

# A good minimal payload that should validate against the schema
GOOD = {
    "population": {"condition": "IBD", "age_criteria": "18–65", "severity": "moderate", "treatment_history": "naive"},
    "intervention": {"type": "drug", "name": "Etrolizumab", "dosage": "105 mg", "route": "subcutaneous", "duration": "52 weeks"},
    "comparison": {"type": "drug", "description": "Infliximab 5 mg/kg intravenously"},
    "outcome": {"efficacy": ["remission"], "quality_of_life": ["IBDQ"], "safety": ["SAE"]},
    "study_design": {"phase": "III", "design": "randomized", "special_notes": "multicenter"},
}


@patch("app.services.analysis_service._call_openai", return_value=(GOOD, {}))
def test_analyze_accepts_dict(mock_call):
    res = analyze_entry_with_openai("dummy abstract", schema_override=PICO_SCHEMA)
    assert res["population"]["condition"] == "IBD"
    mock_call.assert_called_once()


@patch("app.services.analysis_service._call_openai", return_value=(json.dumps(GOOD), {}))
def test_analyze_accepts_json_string(mock_call):
    res = analyze_entry_with_openai("dummy abstract", schema_override=PICO_SCHEMA)
    assert res["intervention"]["name"] == "Etrolizumab"
    mock_call.assert_called_once()


# ---------- Optional integration test (real OpenAI call) ----------
skip_integration = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION"),
    reason="Set RUN_INTEGRATION=1 to run integration tests against OpenAI."
)

@skip_integration
def test_integration_openai_real_call_and_persist_io():
    """
    Full pipeline:
      - parse PDF with pdf_reader (configurable slice)
      - prepend RUN_ID header to the prompt (easy to find in OpenAI logs)
      - send to OpenAI (real call)
      - save input text to reports/inputs/
      - save output JSON to reports/examples/
    """
    from datetime import datetime
    from app.utils.pdf_reader import extract_text_from_pdf

    # 1) Read configuration from environment
    pdf_env = os.getenv("PDF_PATH", str(Path(__file__).parent / "data" / "UC1597_Danese_2022.pdf"))
    start_kw = None if os.getenv("FULL") else os.getenv("START_KW", "methods")
    end_kw = None if os.getenv("FULL") else os.getenv("END_KW", "discussion")
    use_outlines = os.getenv("NO_OUTLINES") is None  # default True unless NO_OUTLINES is set
    remove_headers = os.getenv("KEEP_HEADERS") is None  # default True unless KEEP_HEADERS is set
    normalize_ligatures = os.getenv("NO_LIGATURES") is None
    dehyphen = os.getenv("NO_DEHYPHEN") is None

    schema_path = os.getenv("SCHEMA_PATH", "config/schemas/pico_v1.json")
    schema, schema_sha = load_required_json_schema(schema_path)

    pdf = Path(pdf_env)
    assert pdf.exists(), f"PDF not found: {pdf}"

    # 2) Extract text
    text = extract_text_from_pdf(
        pdf,
        start_kw=start_kw,
        end_kw=end_kw,
        use_outlines=use_outlines,
        remove_headers=remove_headers,
        normalize_ligatures=normalize_ligatures,
        dehyphen=dehyphen,
    )
    assert text and len(text) > 200, "Extraction returned too little text."

    # 3) Prepare RUN_ID and payload
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    slice_tag = ("full" if start_kw is None or end_kw is None else f"{start_kw}-{end_kw}").replace(" ", "_")
    base_name = pdf.stem
    header = (
        f"RUN_ID={run_id} | FILE={base_name} | SLICE={slice_tag} | "
        f"SCHEMA={Path(schema_path).name} | SCHEMA_SHA256={schema_sha[:12]}\n\n"
    )
    payload = header + text

    # 4) Persist input/output
    inputs_dir = Path("reports/inputs"); inputs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir = Path("reports/examples"); outputs_dir.mkdir(parents=True, exist_ok=True)

    input_txt_path = inputs_dir / f"{base_name}_{slice_tag}_{run_id}.txt"
    input_txt_path.write_text(payload, encoding="utf-8")

    # 5) Real OpenAI call
    result = analyze_entry_with_openai(payload, schema_override=schema)

    output_json_path = outputs_dir / f"{base_name}_{slice_tag}_{run_id}.json"
    output_json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8"
    )

    print(f"\nSaved input to:  {input_txt_path}")
    print(f"Saved output to: {output_json_path}")
