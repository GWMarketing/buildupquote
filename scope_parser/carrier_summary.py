"""The document's own bottom-line math -- the "Summary" block a carrier
estimate prints once, after all the line items (Xactimate calls it
"Summary for All Items"; Symbility/Cotality prints an unlabelled version
of the same ladder at the very end of its own totals page). Nothing in
this package read either one before this module existed: every total the
app showed came only from summing individual line items, so a
document-level Overhead/Profit percentage -- a FLAT add-on tied to the
whole estimate, not any one row -- was invisible. That is a real
correctness gap, not a display nicety: on a real contractor export (see
tests/fixtures and the golden fixture "contractor_doyle"), it understates
the document's own total by exactly the printed Overhead + Profit.

Two label vocabularies, one shape: Xactimate's plain "Label  123.45"
lines and Symbility's "Label: $123.45" lines get folded into the same
CarrierSummary fields below -- the same "rule as data, not branches"
pattern as profiles.py, so a third carrier's wording is one more row in
_LABELS, not a new code path.

Anti-guessing rule applies here too: a field is only ever set from an
actual matched line on the actual document. Nothing here is computed,
inferred, or defaulted -- if a document doesn't print "Overhead", this
module never invents one.
"""
import re
from typing import Optional

from .models import CarrierSummary
from .tokens import parse_number

_MONEY_RE = re.compile(r"\(?\$?\(?([\d,]+\.\d{2})\)?\)?")


def _first_amount(line: str) -> Optional[float]:
    """The first dollar-ish figure on the line, as a positive magnitude.

    Every one of these ladders uses parentheses to mean "subtracted from
    the running total" (e.g. "Less Depreciation (92.78)") -- that's a
    fact about the LADDER's arithmetic, not about the number itself, so
    we keep the magnitude here and let the app show each line with its
    own carrier-printed label rather than re-deriving a sign.
    """
    m = _MONEY_RE.search(line)
    if not m:
        return None
    return parse_number(m.group(1))


# (label pattern, field name) -- checked in this order per line, first
# match wins. More specific labels are listed before the shorter labels
# they start with ("Net Claim if Depreciation is Recovered" before plain
# "Net Claim"), otherwise the shorter pattern would grab it first.
#
# "Overhead"/"Profit" require a number (or an opening paren) immediately
# after the label -- without that, a table header like "Overhead Profit
# (10%) Material Sales ..." (Travelers' own tabular O&P recap, a
# different layout this module doesn't attempt to read) would otherwise
# match "Overhead" and then fail to find a number on the SAME line,
# silently skipping the field -- fine either way, but the guard makes the
# intent explicit rather than relying on that fallthrough.
_SUMMARY_FOR_RE = re.compile(r"^Summary for\s*(.*)$", re.IGNORECASE)
# Xactimate's own generic sub-heading, not a coverage name -- printed
# under a coverage-specific "Summary for <coverage>" line, so it never
# becomes the coverage_label itself (see the loop below).
_GENERIC_SUMMARY_HEADING = "all items"

_LABELS = (
    (re.compile(r"^Line Item Total\b", re.IGNORECASE), "line_item_total"),
    (re.compile(r"^Material Sales Tax\b", re.IGNORECASE), "material_sales_tax"),
    (re.compile(r"^Overhead\s+\$?[\d(]", re.IGNORECASE), "overhead"),
    (re.compile(r"^Profit\s+\$?[\d(]", re.IGNORECASE), "profit"),
    (re.compile(r"^Replacement Cost Value\b", re.IGNORECASE), "replacement_cost_value"),
    (re.compile(r"^Less Depreciation\b", re.IGNORECASE), "less_depreciation"),
    (re.compile(r"^(Net )?Actual Cash Value\b", re.IGNORECASE), "actual_cash_value"),
    (re.compile(r"^Less Deductible\b", re.IGNORECASE), "deductible"),
    (re.compile(r"^Deductible\s*\(", re.IGNORECASE), "deductible"),
    (re.compile(r"^Net Claim if Depreciation is Recovered\b", re.IGNORECASE),
     "net_claim_if_depreciation_recovered"),
    (re.compile(r"^Total Recoverable Depreciation\b", re.IGNORECASE),
     "total_recoverable_depreciation"),
    (re.compile(r"^Net Claim\b", re.IGNORECASE), "net_claim"),
    (re.compile(r"^Net Estimate\b", re.IGNORECASE), "net_claim"),
)


def _coverage_label(lines, i):
    """Xactimate prints "Summary for <coverage>" right above the ladder --
    sometimes on one line ("Summary for Dwelling"), sometimes split across
    two ("Summary for" / "AA-Dwelling") when the coverage code pushed it
    past a column wrap. Either way, skip straight past the generic "All
    Items" sub-heading that can follow -- it describes the ladder, not
    which coverage it's scoped to."""
    m = _SUMMARY_FOR_RE.match(lines[i].strip())
    if not m:
        return None
    label = m.group(1).strip()
    if not label and i + 1 < len(lines):
        label = lines[i + 1].strip()
    if not label or label.lower() == _GENERIC_SUMMARY_HEADING:
        return None
    return label


def find_summary(lines) -> Optional[CarrierSummary]:
    """Walk the same noise-filtered, boilerplate-excluded lines the rest
    of the pipeline works from (so a "how to read your estimate" insert
    page can't fake a summary block) and pull out whatever ladder lines
    this document actually prints. Returns None when nothing matched --
    a format this module doesn't yet recognise, not an error."""
    found = {}
    source_label = "Net Claim"
    coverage_label = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if coverage_label is None:
            label = _coverage_label(lines, i)
            if label:
                coverage_label = label
        for pattern, field_name in _LABELS:
            if not pattern.match(line):
                continue
            value = _first_amount(line)
            if value is None:
                break
            # First match wins -- guards against a repeated/rollup line
            # later in the document overwriting the real summary figure.
            if field_name not in found:
                found[field_name] = value
                if field_name == "net_claim" and line.lower().startswith("net estimate"):
                    source_label = "Net Estimate"
            break
    if not found:
        return None
    return CarrierSummary(source_label=source_label, coverage_label=coverage_label, **found)
