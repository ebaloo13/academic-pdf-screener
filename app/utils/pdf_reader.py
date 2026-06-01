# app/utils/pdf_reader.py
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import fitz  # PyMuPDF
from app.core.logging_config import logger

# ---------- normalization helpers ----------

_LIGATURE_MAP: Dict[str, str] = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "–": "-", "—": "-", "−": "-",
}

def _normalize_ligatures(text: str) -> str:
    for k, v in _LIGATURE_MAP.items():
        text = text.replace(k, v)
    return text

def _dehyphenate(text: str) -> str:
    # join words split across line breaks: e.g., "treat-\nment" -> "treatment"
    return re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)


def _strip_headers_footers(page_text: str) -> str:
    """
    Remove common medical-journal headers/footers (best-effort, conservative).
    We avoid over-stripping by requiring uppercase-ish patterns near edges.
    """
    lines = page_text.splitlines()
    cleaned: List[str] = []
    for line in lines:
        u = line.strip().upper()
        if not u:
            cleaned.append(line)
            continue

        # Typical journal noise / mastheads / page numbers
        if "WWW.THELANCET.COM/GASTROHEP" in u:
            continue
        if re.search(r"\bVOLUME\s+\d+\b.*\b\d{4}\b", u):
            continue
        if re.search(r"\bTHE AMERICAN JOURNAL OF GASTROENTEROLOGY\b", u):
            continue
        if re.match(r"^\d+\s*$", u):  # page number line
            continue
        # Section ribbons sometimes printed in page gutter:
        if u in {"ORIGINAL ARTICLE", "RESEARCH ARTICLE", "INFLAMMATORY BOWEL DISEASE"}:
            continue

        cleaned.append(line)
    return "\n".join(cleaned)


# ---------- slicing helpers ----------

def _find_kw_occurrence(text: str, kw: str, occurrence: int) -> int:
    """
    Find the byte index for the Nth case-insensitive occurrence of 'kw' in 'text'.
    Returns -1 if not found.
    """
    if kw is None:
        return -1
    kw_l = kw.lower()
    text_l = text.lower()
    start = 0
    count = 0
    while True:
        idx = text_l.find(kw_l, start)
        if idx == -1:
            return -1
        count += 1
        if count == max(1, occurrence or 1):
            return idx
        start = idx + 1


def _slice_by_keywords(
    text: str,
    start_kw: Optional[str],
    end_kw: Optional[str],
    *,
    start_occurrence: int = 1,
    end_occurrence: int = 1,
) -> str:
    """
    Slice [start_kw .. end_kw] inclusive (case-insensitive). If either is missing, return full text.
    """
    if not start_kw or not end_kw:
        return text

    s = _find_kw_occurrence(text, start_kw, start_occurrence)
    e = _find_kw_occurrence(text, end_kw, end_occurrence)
    if s == -1 or e == -1 or e <= s:
        return text
    return text[s: e + len(end_kw)]


# ---------- block ordering (two columns) ----------

def _block_text(block: Dict) -> str:
    lines: List[str] = []
    for line in block.get("lines", []):
        txt = " ".join(span.get("text", "") for span in line.get("spans", []))
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            lines.append(txt)
    return "\n".join(lines)


def _order_blocks_two_columns(page: fitz.Page, blocks: List[Dict], *, enforce_two_columns: bool) -> List[Dict]:
    """
    Detect a two-column layout by the largest horizontal gap between block centers.
    If the gap is wide (>= 18% page width) AND both sides have ≥25% of blocks (and ≥2 each),
    read left (top→bottom) then right (top→bottom). Otherwise use (y,x) order.
    If enforce_two_columns=True, split at mid-page regardless of detection.
    """
    if len(blocks) < 2:
        return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))

    page_width = float(page.rect.width)
    centers = sorted(
        (((b["bbox"][0] + b["bbox"][2]) / 2.0), i) for i, b in enumerate(blocks)
    )

    if enforce_two_columns:
        threshold = page_width / 2.0
        left_idxs = {i for x, i in centers if x <= threshold}
        right_idxs = {i for x, i in centers if x > threshold}
    else:
        # auto-detect split by largest horizontal gap
        max_gap: float = -1.0
        max_idx: int = -1
        for i in range(len(centers) - 1):
            gap = centers[i + 1][0] - centers[i][0]
            if gap > max_gap:
                max_gap, max_idx = gap, i

        # Decide if this page likely has two columns
        if max_gap >= 0.18 * page_width:
            threshold = (centers[max_idx][0] + centers[max_idx + 1][0]) / 2.0
            left_idxs = {i for x, i in centers if x <= threshold}
            right_idxs = {i for x, i in centers if x > threshold}
            # sanity: both sides must be non-trivial
            if not (len(left_idxs) >= 2 and len(right_idxs) >= 2):
                return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            if not (len(left_idxs) >= 0.25 * len(blocks) and len(right_idxs) >= 0.25 * len(blocks)):
                return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        else:
            return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))

    left_blocks = [blocks[i] for i in left_idxs]
    right_blocks = [blocks[i] for i in right_idxs]
    left_sorted = sorted(left_blocks, key=lambda b: b["bbox"][1])
    right_sorted = sorted(right_blocks, key=lambda b: b["bbox"][1])
    return left_sorted + right_sorted


def _page_to_text(
    page: fitz.Page,
    *,
    skip_header_matches: bool,
    enforce_two_columns: bool,
    skip_captions: bool,
) -> str:
    """
    Convert a page to text honoring two-column layouts when detected / enforced.
    """
    page_dict = page.get_text("dict")
    raw_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
    ordered = _order_blocks_two_columns(page, raw_blocks, enforce_two_columns=enforce_two_columns)

    lines: List[str] = []
    for block in ordered:
        bt = _block_text(block)
        if not bt:
            continue

        # Drop lone giant section labels printed in margin gutters
        if skip_header_matches:
            u = bt.strip().upper()
            if u in {"METHODS", "METHODS:", "RESULTS", "RESULTS:", "DISCUSSION", "DISCUSSION:"} and len(bt) <= 20:
                continue

        # Optionally skip figure/table captions (often noise for PICO)
        if skip_captions:
            first = bt.strip()
            if re.match(r"^(Figure|Fig\.|Table)\s+\d+[:.\s]", first, flags=re.IGNORECASE):
                continue

        lines.append(bt)

    return "\n".join(lines)


# ---------- outlines-based slice (bookmarks) ----------

def _slice_by_outline_pages(
    doc: fitz.Document,
    pages_text: List[str],
    *,
    start_kw: Optional[str],
    end_kw: Optional[str],
    skip_pages: int,
    start_occurrence: int,
    end_occurrence: int,
) -> Optional[str]:
    """
    Try to slice using PDF outlines (bookmarks). Honors skip_pages and N-th occurrence.
    Returns None if start/end not found in order.
    """
    if not start_kw or not end_kw:
        return None

    try:
        toc = doc.get_toc(simple=False)  # list of (level, title, page, ...)
    except Exception:
        return None

    def _find_page_for_kw(kw: str, nth: int) -> Optional[int]:
        count = 0
        kw_l = kw.lower()
        for _, title, page_num, *_ in toc:
            title_l = (title or "").lower()
            if kw_l in title_l:
                count += 1
                if count == max(1, nth or 1):
                    return max(1, page_num)
        return None

    sp = _find_page_for_kw(start_kw, start_occurrence)
    ep = _find_page_for_kw(end_kw, end_occurrence)
    if sp is None or ep is None or ep <= sp:
        return None

    # Adjust for skip_pages: pages_text starts AFTER skipping
    # doc pages are 1-based; pages_text[0] == doc page (1 + skip_pages)
    start_idx = sp - 1 - max(0, skip_pages)
    end_idx_excl = ep - max(0, skip_pages)

    if start_idx < 0 or end_idx_excl <= start_idx or start_idx >= len(pages_text):
        return None

    slice_pages = pages_text[start_idx: end_idx_excl]
    return "\n\n".join(slice_pages).strip() or None


# ---------- public API ----------

def extract_text_from_pdf(
    pdf_path: str | Path,
    *,
    start_kw: Optional[str] = "methods",
    end_kw: Optional[str] = "discussion",
    use_outlines: bool = True,
    remove_headers: bool = True,
    normalize_ligatures: bool = True,
    dehyphen: bool = True,
    skip_pages: int = 0,
    skip_header_matches: bool = True,
    start_occurrence: int = 1,
    end_occurrence: int = 1,
    enforce_two_columns: bool = False,
    skip_captions: bool = True,
) -> Optional[str]:
    """
    Extract plain text from academic PDFs with robust two-column ordering and optional slicing.

    Key options:
    - start_kw / end_kw (+ occurrences): slice between headings (inclusive).
    - use_outlines: prefer bookmark-based slice (fast/precise) when available.
    - remove_headers: strip repeated header/footer noise.
    - normalize_ligatures / dehyphen: readability / tokenization improvements.
    - skip_pages: ignore first N pages (skip title, ToC, front matter).
    - enforce_two_columns: force 2-column ordering when auto-detect is unreliable.
    - skip_captions: drop Figure/Table captions.
    """
    try:
        pdf_path = Path(pdf_path)
        with fitz.open(pdf_path) as doc:
            pages_text: List[str] = []

            for i, page in enumerate(doc):
                if i < max(0, skip_pages):
                    continue
                txt = _page_to_text(
                    page,
                    skip_header_matches=skip_header_matches,
                    enforce_two_columns=enforce_two_columns,
                    skip_captions=skip_captions,
                )
                if remove_headers:
                    txt = _strip_headers_footers(txt)
                pages_text.append(txt)

            full_text = "\n\n".join(pages_text).strip()
            if normalize_ligatures:
                full_text = _normalize_ligatures(full_text)
            if dehyphen:
                full_text = _dehyphenate(full_text)

            # Try outline (bookmarks) slice first
            if use_outlines:
                outline_slice = _slice_by_outline_pages(
                    doc,
                    pages_text,
                    start_kw=start_kw,
                    end_kw=end_kw,
                    skip_pages=skip_pages,
                    start_occurrence=start_occurrence,
                    end_occurrence=end_occurrence,
                )
                if outline_slice:
                    return outline_slice

            # Fallback: keyword slice over full_text
            if start_kw and end_kw:
                sliced = _slice_by_keywords(
                    full_text,
                    start_kw,
                    end_kw,
                    start_occurrence=start_occurrence,
                    end_occurrence=end_occurrence,
                )
                return sliced or full_text

            # No slicing requested
            return full_text or None

    except Exception as err:
        logger.error("Failed reading PDF %s", pdf_path, exc_info=err)
        return None
