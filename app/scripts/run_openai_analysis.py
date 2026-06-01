# app/scripts/run_openai_analysis.py
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import fitz  # PyMuPDF

from app.core.logging_config import logger
from app.core.schema_loader import load_required_json_schema
from app.utils.pdf_reader import extract_text_from_pdf
from app.services.analysis_service import analyze_entry_with_openai_with_usage

# ---------------- Quick knobs (testing) ----------------
DEFAULT_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TOP_P = 0.1



def _pdf_meta(pdf_path: Path) -> Dict[str, Any]:
    try:
        with fitz.open(pdf_path) as doc:
            md = doc.metadata or {}
            title = (md.get("title") or md.get("Title") or "").strip() or None
            author = (md.get("author") or md.get("Author") or "").strip() or None
            return {"title": title, "author": author}
    except Exception:
        return {"title": None, "author": None}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run OpenAI PICO extraction over a PDF slice.")
    p.add_argument("pdf", type=Path, help="Path to input PDF")
    p.add_argument("--schema", type=Path, required=True, help="Path to JSON schema (required)")
    p.add_argument("--start", default="methods", help="Slice starts at this heading (default: methods)")
    p.add_argument("--end", default="discussion", help="Slice ends at this heading (default: discussion)")
    p.add_argument("--start-occurrence", type=int, default=2, help="Use the N-th occurrence of --start (default: 2)")
    p.add_argument("--skip-pages", type=int, default=1, help="Skip first K pages before searching (default: 1)")
    p.add_argument("--no-outlines", action="store_true", help="(kept for back-compat; ignored in text slicing)")
    p.add_argument("--keep-headers", action="store_true", help="Do NOT remove headers/footers")
    p.add_argument("--no-ligatures", action="store_true", help="Do NOT normalize ligatures")
    p.add_argument("--no-dehyphen", action="store_true", help="Do NOT join hyphenated words")

    # NEW: param overrides (optional). If omitted, use script constants above.
    p.add_argument("--model", default=None, help="Override model (default uses script constant)")
    p.add_argument("--temperature", type=float, default=None, help="Override temperature (default uses script constant)")
    p.add_argument("--top-p", dest="top_p", type=float, default=None, help="Override top_p (default uses script constant)")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    pdf_path: Path = args.pdf
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"

    schema_dict, schema_sha = load_required_json_schema(args.schema)

    text = extract_text_from_pdf(
        pdf_path,
        start_kw=args.start,
        end_kw=args.end,
        use_outlines=not args.no_outlines,   # harmless keep
        remove_headers=not args.keep_headers,
        normalize_ligatures=not args.no_ligatures,
        dehyphen=not args.no_dehyphen,
        start_occurrence=args.start_occurrence,
        skip_pages=args.skip_pages,
    )
    if not text or len(text) < 200:
        raise RuntimeError("Extraction returned too little text. Try --start-occurrence 2 or --skip-pages 1.")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    slice_tag = f"{args.start}-{args.end}-occ{args.start_occurrence}-skip{args.skip_pages}".replace(" ", "_")
    schema_name = args.schema.name

    header = (
        f"RUN_ID={run_id} | FILE={pdf_path.stem} | SLICE={slice_tag} "
        f"| SCHEMA={schema_name} | SCHEMA_SHA256={schema_sha}\n\n"
    )

    preview = (header + text)[:800]
    print(f"\nPreview (800 chars):\n{preview}\n")

    inputs_dir = Path("reports/inputs"); inputs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir = Path("reports/examples"); outputs_dir.mkdir(parents=True, exist_ok=True)

    input_txt_path = inputs_dir / f"{pdf_path.stem}_{slice_tag}_{run_id}.txt"
    input_txt_path.write_text(header + text, encoding="utf-8")

    # Effective params = CLI override if provided; else script constants
    eff_model = args.model or DEFAULT_MODEL
    eff_temp = DEFAULT_TEMPERATURE if args.temperature is None else args.temperature
    eff_top_p = DEFAULT_TOP_P if args.top_p is None else args.top_p

    pico, usage = analyze_entry_with_openai_with_usage(
        entry=header + text,
        model=eff_model,
        temperature=eff_temp,
        top_p=eff_top_p,
        schema_override=schema_dict,
    )

    assert isinstance(pico, dict) and pico, "Empty or invalid PICO payload."
    print("PICO top-level keys:", ", ".join(pico.keys()))

    meta = _pdf_meta(pdf_path)
    enriched: Dict[str, Any] = {
        "file": {"name": pdf_path.stem, "path": str(pdf_path), "title": meta["title"], "author": meta["author"]},
        "slice": {
            "start": args.start, "end": args.end,
            "start_occurrence": args.start_occurrence, "skip_pages": args.skip_pages,
            "use_outlines": not args.no_outlines, "remove_headers": not args.keep_headers,
            "normalize_ligatures": not args.no_ligatures, "dehyphen": not args.no_dehyphen,
        },
        "params": {"model": eff_model, "temperature": eff_temp, "top_p": eff_top_p},
        "schema": {"file": schema_name, "sha256": schema_sha},
        "usage": usage,
        "pico": pico,
    }

    out_json_path = outputs_dir / f"{pdf_path.stem}_{slice_tag}_{run_id}.json"
    out_json_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDone.")
    print("Input saved to: ", input_txt_path)
    print("Output saved to:", out_json_path)
    print("Search RUN_ID in your OpenAI dashboard (Logs/Responses).")


if __name__ == "__main__":
    main()
