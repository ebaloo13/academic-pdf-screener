# app/utils/xls_reader.py
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd


def load_rows_from_excel(
    excel_path: str | Path,
    *,
    sheet_name: Optional[str] = None,
    text_column: str = "abstract",
    id_column: Optional[str] = None,
    drop_empty: bool = True,
    limit: Optional[int] = None,
) -> List[Dict[str, str]]:
    """
    Load rows from an Excel file and return a list of {"row_id": ..., "text": ...} dicts.

    Args:
        excel_path: Path to the .xlsx file.
        sheet_name: Sheet to read. If None, use the first sheet.
        text_column: Column name containing the abstract text.
        id_column: Optional column to use as stable ID. If None, Excel row index is used.
        drop_empty: Drop rows where text_column is empty or NaN.
        limit: Optional maximum number of rows to return.

    Returns:
        List of dicts: [{"row_id": "<id or idx>", "text": "<abstract>"}]
    """
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    if text_column not in df.columns:
        raise ValueError(f"Column '{text_column}' not found. Available: {list(df.columns)}")

    # Normalize text column to strings
    df[text_column] = df[text_column].fillna("").astype(str).map(lambda s: s.strip())

    if drop_empty:
        df = df[df[text_column] != ""]

    rows: List[Dict[str, str]] = []
    for idx, row in df.iterrows():
        rid = str(row[id_column]).strip() if id_column and id_column in df.columns else str(idx)
        txt = row[text_column]
        rows.append({"row_id": rid, "text": txt})

    if limit is not None:
        rows = rows[: int(limit)]

    return rows
