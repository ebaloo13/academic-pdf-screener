# tests/test_pdf_reader.py
from pathlib import Path
import pytest

from app.utils.pdf_reader import extract_text_from_pdf

PDF = Path(__file__).parent / "data" / "UC1597_Danese_2022.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="PDF fixture is not available")


def test_full_text_not_none_and_long_enough():
    text = extract_text_from_pdf(PDF, start_kw=None, end_kw=None)
    assert text is not None, "Extractor returned None"
    assert len(text) > 1000, "Full text too short – extraction likely failed"


def test_methods_to_discussion_slice_is_inclusive_and_shorter():
    # Default slice: Methods → Discussion (inclusive)
    sliced = extract_text_from_pdf(PDF, start_occurrence=2)
    full = extract_text_from_pdf(PDF, start_kw=None, end_kw=None)

    assert sliced is not None and full is not None
    U = sliced.upper()
    assert "METHODS" in U[:1200], "Slice should start near 'Methods'"
    assert "DISCUSSION" in U[-2000:], "Slice should include 'Discussion' near the end"
    assert len(sliced) <= len(full), "Slice should not be longer than full text"


def test_header_footer_removal_reduces_repeated_noise():
    # Compare with and without header removal using a known Lancet header pattern
    full_nohdr = extract_text_from_pdf(PDF, start_kw=None, end_kw=None, remove_headers=True)
    full_withhdr = extract_text_from_pdf(PDF, start_kw=None, end_kw=None, remove_headers=False)

    assert full_nohdr is not None and full_withhdr is not None

    needle = "WWW.THELANCET.COM/GASTROHEP"
    count_nohdr = full_nohdr.upper().count(needle)
    count_withhdr = full_withhdr.upper().count(needle)

    # Expect fewer (or equal) occurrences after stripping headers; typically strictly fewer
    assert count_nohdr <= count_withhdr
    assert (count_withhdr - count_nohdr) >= 1, "Expected at least one header/footer to be removed"


def test_missing_markers_falls_back_to_full_text():
    full = extract_text_from_pdf(PDF, start_kw=None, end_kw=None)
    sliced = extract_text_from_pdf(PDF, start_kw="no-such", end_kw="also-none")
    assert sliced == full, "When markers are missing, function should return the full text"
