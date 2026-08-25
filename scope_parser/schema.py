"""Header-driven column detection.

Different carriers (and even different sections of the same estimate --
see the Travelers sample, which adds an O&P column only for some trades)
print different sets of columns after QUANTITY/UNIT. Rather than hard-code
one column layout, we read the printed header row for each section and
build the column list from it. See the project's "Known parsing problems"
doc, problem #5 (O&P) for why this matters.

One extra wrinkle, confirmed against real samples: several carriers print
a header that OMITS the per-unit price column even though the data rows
include it (Allstate and Travelers both do this; the appraiser/"Property
Insurance Experts" template does not -- it prints PRICE explicitly). We
correct for that below rather than mis-mapping every row that has one more
number than the printed header admits to.
"""
import re

from .profiles import XACTIMATE

# Canonical column name -> header tokens that mean it. Order doesn't matter
# here; the ORDER on the printed line is what determines final column order.
#
# NOTE: these two constants now live on the rule sheet
# (profiles.XACTIMATE) and are kept here only so that any existing import
# of them keeps working. The functions below read the sheet, not these.
_HEADER_TOKEN_TO_FIELD = {
    "PRICE": "unit_price",
    "TAX": "tax",
    "O&P": "overhead_profit",
    "RCV": "rcv",
    "AGE/LIFE": "age_life",
    "COND.": "condition",
    "COND": "condition",
    "DEP%": "depreciation_pct",
    "DEPREC.": "depreciation",
    "DEPREC": "depreciation",
    "ACV": "acv",
}

_IGNORED_HEADER_TOKENS = {"DESCRIPTION", "QUANTITY", "UNIT"}


def _normalize(line: str) -> str:
    # "DEP %" -> "DEP%" so it tokenizes as one column marker.
    line = re.sub(r"\bDEP\s*%", "DEP%", line.upper())
    return line.strip()


# Column names that mean "this row's costs are printed as several action
# columns that have to be added together", not "this is the unit price".
ACTION_FIELDS = ("action_reset", "action_remove", "action_replace")


def is_header_line(line: str, profile=XACTIMATE) -> bool:
    norm = _normalize(line)
    tokens = norm.split()
    # A quantity column is required. A UNIT column is not: Xactimate's
    # contractor-facing export prints "DESCRIPTION QTY RESET REMOVE
    # REPLACE *TOTAL" and puts the unit inline with the quantity
    # ("172.50 SF") instead of giving it a column of its own.
    if not ({"QUANTITY", "QTY"} & set(tokens)):
        return False
    # Every token must be something we recognize -- a stray real header
    # from a different template should fail closed (treated as not a
    # header) rather than being guessed at.
    return all(
        t in profile.ignored_header_tokens or t in profile.header_token_to_field
        for t in tokens
    )


def parse_header(line: str, profile=XACTIMATE):
    """Return the ordered list of field names that follow quantity/unit.

    Inserts "unit_price" first if the header didn't mention PRICE and the
    rule sheet says this format needs that correction -- see module
    docstring. Returns None if `line` isn't a header for this format.
    """
    if not is_header_line(line, profile):
        return None
    norm = _normalize(line)
    tokens = [t for t in norm.split() if t not in profile.ignored_header_tokens]
    fields = [profile.header_token_to_field[t] for t in tokens]
    # The missing-PRICE correction applies only to layouts that HAVE a
    # single price column. An action-column layout has no unit price
    # printed at all -- it is the sum of the action columns.
    if any(f in ACTION_FIELDS for f in fields):
        return fields
    if profile.insert_missing_unit_price and "unit_price" not in fields:
        fields = ["unit_price"] + fields
    return fields


DEFAULT_SCHEMA = ["unit_price", "rcv", "acv"]
