# Journal API

FastAPI service and CLI tools for extracting structured PICO/PICOS data from academic journal articles. The pipeline reads PDF or Excel inputs, extracts text, sends it to OpenAI Structured Outputs with a required JSON Schema, validates the response, and optionally writes traceable JSON/CSV reports.

## Features

- Academic PDF text extraction with two-column ordering, header/footer cleanup, ligature normalization, and dehyphenation.
- Optional section slicing, usually `methods` to `discussion`, with occurrence controls.
- OpenAI Structured Outputs using a required local JSON Schema.
- FastAPI endpoints for text and PDF analysis.
- CLI scripts for single-PDF analysis and Excel batch processing.
- Pytest coverage for PDF extraction and mocked OpenAI analysis.

## Project Structure

```text
Journal-api/
├─ app/
│  ├─ main.py                    FastAPI app entrypoint
│  ├─ api/endpoints.py           /analyze-text and /analyze-pdf routes
│  ├─ core/
│  │  ├─ config.py               environment loading
│  │  ├─ logging_config.py       shared logger
│  │  └─ schema_loader.py        JSON Schema loading and validation
│  ├─ services/analysis_service.py
│  ├─ utils/
│  │  ├─ pdf_reader.py
│  │  └─ xls_reader.py
│  └─ scripts/
│     ├─ run_openai_analysis.py
│     └─ run_excel_batch.py
├─ config/schemas/               PICO/PICOS JSON Schemas
├─ tests/                        pytest tests
├─ requirements.txt
├─ .env.example
└─ README.md
```

Local folders such as `data/`, `reports/`, `.env`, `.venv/`, and cache files are intentionally ignored by Git because they can contain private documents, generated outputs, dependencies, or secrets.

## Setup

Use Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` and set:

```bash
OPENAI_API_KEY=your_openai_api_key_here
```

Never commit `.env` or real API keys.

## Run The API

```bash
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Analyze text:

```bash
curl -X POST http://127.0.0.1:8000/analyze-text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Paste article abstract or extracted article text here.",
    "schema_path": "config/schemas/pico_v1.json"
  }'
```

Analyze a local PDF:

```bash
curl -X POST http://127.0.0.1:8000/analyze-pdf \
  -H "Content-Type: application/json" \
  -d '{
    "pdf_path": "data/pdf/example.pdf",
    "schema_path": "config/schemas/pico_v1.json",
    "start_kw": "methods",
    "end_kw": "discussion",
    "start_occurrence": 1
  }'
```

## CLI Usage

Single PDF:

```bash
python -m app.scripts.run_openai_analysis data/pdf/example.pdf \
  --schema config/schemas/pico_v1.json
```

Excel batch:

```bash
python -m app.scripts.run_excel_batch data/xls/input.xlsx \
  --schema config/schemas/pico_v1.json \
  --text-col abstract
```

When scripts write results, they go under `reports/`, which is ignored by Git.

## Tests

```bash
pytest
```

The default tests mock OpenAI calls. To run the optional real OpenAI integration test:

```bash
RUN_INTEGRATION=1 pytest tests/test_pdf_analysis.py
```

## Before Publishing To GitHub

1. Rotate the OpenAI API key that was stored in `.env`.
2. Confirm `.env`, `data/`, `reports/`, `.venv/`, caches, and private PDFs are not staged.
3. Remove or resolve the nested Git repository currently inside `app/.git` before creating the root repository.
4. Review whether any PDFs, Excel files, Word documents, or report outputs are private or copyrighted.
5. Run tests in a clean macOS/Linux virtual environment.

## Notes

- The API currently allows all CORS origins for local development. Tighten `allow_origins` in `app/main.py` before production deployment.
- Default model selection lives in `app/services/analysis_service.py` and the CLI scripts.
- JSON Schemas are mandatory; the service does not use a fallback schema.
