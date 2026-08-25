"""BuildUpQuote parsing engine.

Turns a carrier estimate PDF (Xactimate-style and similar) into clean,
structured data: claim metadata, line items (with insurance-only columns
like ACV/depreciation kept but clearly separated from the contractor-facing
fields), and roof/room measurement blocks -- with CAD sketch noise stripped
out along the way.

This package deliberately knows nothing about any UI. See pipeline.py for
the single entry point most callers want: parse_text() / parse_pdf().
"""

from .pipeline import parse_text, parse_pdf
from .models import (
    CarrierSummary,
    ClaimFlags,
    ClaimMetadata,
    LineItem,
    MeasurementBlock,
    ParsedEstimate,
    SectionTotals,
)

__all__ = [
    "parse_text",
    "parse_pdf",
    "CarrierSummary",
    "ClaimFlags",
    "LineItem",
    "MeasurementBlock",
    "ClaimMetadata",
    "ParsedEstimate",
    "SectionTotals",
]
