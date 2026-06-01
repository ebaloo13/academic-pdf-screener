# scripts/run_excel_batch.py
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.core.logging_config import logger
from app.core.schema_loader import load_required_json_schema
from app.utils.xls_reader import load_rows_from_excel
from app.services.analysis_service import analyze_entry_with_openai


def _flatten_pico(pico: Dict[str, Any]) -> Dict[str, str]:
    """Flatten nested PICO JSON into a flat dict of strings for CSV export."""
    def get(d: Dict, *keys, default: str = "") -> str:
        cur: Any = d
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur if isinstance(cur, str) else default

    def join_list(d: Dict, *keys) -> str:
        cur: Any = d
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return ""
            cur = cur[k]
        if isinstance(cur, list):
            return "; ".join([str(x).strip() for x in cur if isinstance(x, str)])
        return ""

    flat = {
        # population
        "population_condition": get(pico, "population", "condition"),
        "population_age_criteria": get(pico, "population", "age_criteria"),
        "population_severity": get(pico, "population", "severity"),
        "population_treatment_history": get(pico, "population", "treatment_history"),
        # intervention
        "intervention_type": get(pico, "intervention", "type"),
        "intervention_name": get(pico, "intervention", "name"),
        "intervention_dosage": get(pico, "intervention", "dosage"),
        "intervention_route": get(pico, "intervention", "route"),
        "intervention_duration": get(pico, "intervention", "duration"),
        # comparison
        "comparison_type": get(pico, "comparison", "type"),
        "comparison_description": get(pico, "comparison", "description"),
        # outcomes
        "outcome_efficacy": join_list(pico, "outcome", "efficacy"),
        "outcome_quality_of_life": join_list(pico, "outcome", "quality_of_life"),
        "outcome_safety": join_list(pico, "outcome", "safety"),
        # study design
        "study_design_phase": get(pico, "study_design", "phase"),
        "study_design_design": get(pico, "study_design", "design"),
        "study_design_special_notes": get(pico, "study_design", "special_notes"),
    }
    return flat


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch: read abstracts from Excel and send each to OpenAI for PICO extraction."
    )
    parser.add_argument("excel", type=str, help="Path to the Excel file (.xlsx)")
    parser.add_argument("--sheet", type=str, default=None, help="Sheet name (default: first sheet)")
    parser.add_argument("--text-col", type=str, default="abstract", help="Column name with abstracts (default: 'abstract')")
    parser.add_argument("--id-col", type=str, default=None, help="Optional ID column")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N rows")
    parser.add_argument("--preview", type=int, default=500, help="Print first N chars of each payload (default: 500)")

    # Schema is mandatory (no fallback)
    parser.add_argument("--schema", required=True, help="Path to JSON Schema file (required)")

    args = parser.parse_args()
    excel_path = Path(args.excel).resolve()
    if not excel_path.exists():
        logger.error("Excel not found: %s", excel_path)
        return 2

    # Load and validate schema (also get SHA for traceability)
    schema, schema_sha = load_required_json_schema(args.schema)

    # Load rows from Excel
    rows = load_rows_from_excel(
        excel_path,
        sheet_name=args.sheet,
        text_column=args.text_col,
        id_column=args.id_col,
        drop_empty=True,
        limit=args.limit,
    )
    if not rows:
        logger.error("No rows with non-empty abstracts.")
        return 3

    # Output directory for this batch
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("reports/batches") / f"{excel_path.stem}_{batch_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs_jsonl = out_dir / "inputs.jsonl"
    outputs_jsonl = out_dir / "outputs.jsonl"
    outputs_csv = out_dir / "outputs.csv"
    errors_jsonl = out_dir / "errors.jsonl"

    # Open files
    f_in = inputs_jsonl.open("w", encoding="utf-8")
    f_out = outputs_jsonl.open("w", encoding="utf-8")
    f_err = errors_jsonl.open("w", encoding="utf-8")

    # CSV writer with stable columns
    fieldnames = [
        "run_id", "row_id",
        "population_condition", "population_age_criteria", "population_severity", "population_treatment_history",
        "intervention_type", "intervention_name", "intervention_dosage", "intervention_route", "intervention_duration",
        "comparison_type", "comparison_description",
        "outcome_efficacy", "outcome_quality_of_life", "outcome_safety",
        "study_design_phase", "study_design_design", "study_design_special_notes",
    ]
    csv_f = outputs_csv.open("w", encoding="utf-8", newline="")
    csv_w = csv.DictWriter(csv_f, fieldnames=fieldnames)
    csv_w.writeheader()

    processed = 0
    for i, row in enumerate(rows, start=1):
        row_id = row["row_id"]
        text = row["text"]
        run_id = f"{batch_id}-{i:05d}"

        # Build payload header for traceability (searchable in OpenAI logs)
        header = (
            f"RUN_ID={run_id} | SOURCE={excel_path.stem} | ROW={row_id} | "
            f"SCHEMA={Path(args.schema).name} | SCHEMA_SHA256={schema_sha[:12]}\n\n"
        )
        payload = header + text

        # Persist exact input (compact one-line JSON per row)
        f_in.write(json.dumps(
            {"run_id": run_id, "row_id": row_id, "input_text": payload},
            ensure_ascii=False, sort_keys=True
        ) + "\n")

        # Optional preview to console
        preview = payload[:max(0, int(args.preview))].replace("\n", "\\n")
        print(f"\n[{i}/{len(rows)}] RUN_ID={run_id} ROW={row_id} PREVIEW={preview[:120]}...")

        try:
            pico = analyze_entry_with_openai(payload, schema_override=schema)  # real OpenAI call
            # Persist raw JSON per row
            f_out.write(json.dumps(
                {"run_id": run_id, "row_id": row_id, "pico": pico},
                ensure_ascii=False, sort_keys=True
            ) + "\n")

            # Write flat CSV row
            flat = _flatten_pico(pico)
            flat_row = {"run_id": run_id, "row_id": row_id, **flat}
            csv_w.writerow(flat_row)

            processed += 1

        except Exception as e:
            logger.exception("Failed row row_id=%s run_id=%s", row_id, run_id)
            f_err.write(json.dumps(
                {"run_id": run_id, "row_id": row_id, "error": str(e)},
                ensure_ascii=False, sort_keys=True
            ) + "\n")
            continue

    # Close files
    f_in.close()
    f_out.close()
    f_err.close()
    csv_f.close()

    print(f"\nBatch done. Processed: {processed}/{len(rows)}")
    print(f"Inputs:  {inputs_jsonl}")
    print(f"Outputs: {outputs_jsonl}")
    print(f"CSV:     {outputs_csv}")
    print(f"Errors:  {errors_jsonl} (only if any failure)")
    print("Search RUN_IDs in your OpenAI dashboard (Logs/Responses).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
