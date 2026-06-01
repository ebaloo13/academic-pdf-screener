# app/core/config.py
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root: JOURNAL-API/
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").upper()
