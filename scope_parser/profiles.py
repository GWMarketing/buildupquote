"""Format rule sheets.

A `Profile` is every format-specific decision this parser makes, gathered
into one place as DATA. It contains no logic. The engine reads a sheet;
the sheet never reads the engine.

That distinction is the whole safety argument for supporting more than one
estimating program. The alternative -- writing `if program == "cotality":`
inside the six modules that currently hold these values -- would thread the
Xactimate path with branches that only ever run for other formats, and
every future change would have to reason about all of them at once. A rule
sheet cannot do that. It has no ability to change behaviour for anyone but
itself.

The Xactimate sheet below was built by MOVING the constants out of
units.py, schema.py, line_items.py and extract.py -- not by retyping them.
Same values, same code, same output, which is what the golden-snapshot
lock in tests/ exists to prove rather than assume.

See `claude/multi-format-architecture.md` and the "The Format Layer"
reference doc for the design this implements.
"""
from dataclasses import dataclass, field
from typing import Optional, Pattern

from .units import GENERIC_UNIT_TOKENS, XACTIMATE_UNIT_TOKENS

import re


@dataclass(frozen=True)
class Profile:
    """One estimating program's printed conventions."""

    key: str
    label: str  # what the contractor sees, e.g. "Xactimate"

    # -- how a row is recognised ------------------------------------
    unit_tokens: frozenset
    # Pattern for a numbered row start. Group 1 = number, group 2 =
    # optional letter suffix, group 3 = the rest of the line. None means
    # this format doesn't number its rows and the quantity/unit anchor has
    # to double as the row start (see generic_reader.py).
    item_number_re: Optional[Pattern]
    totals_re: Pattern
    continued_re: Optional[Pattern]
    page_furniture_re: Pattern
    note_triggers: Optional[Pattern]
    # A wrapped description fragment is at most this many words. Formats
    # wrap at different column widths, so this is a per-sheet number.
    max_continuation_words: int = 2

    # -- how the columns after quantity/unit are read ----------------
    # "header" = read the printed header row and map column names (what
    # Xactimate-family documents allow). "arithmetic" = solve for the
    # price and total columns by testing qty x price = total across the
    # whole section (what the generic reader does when there are no header
    # names it recognises).
    column_strategy: str = "header"
    header_token_to_field: dict = field(default_factory=dict)
    ignored_header_tokens: frozenset = frozenset()
    # Several carriers print a header that omits the per-unit PRICE column
    # even though the data rows include it. True = correct for that.
    insert_missing_unit_price: bool = True
    default_schema: tuple = ()

    # -- pages to drop before parsing sees them ----------------------
    boilerplate_page_markers: tuple = ()

    # -- claim-math conventions --------------------------------------
    # True = this program computes sales tax INTO its own estimate total,
    # so the contractor's tax must not be stacked on top of it.
    carrier_tax_included: bool = False

    # -- fingerprinting ----------------------------------------------
    # Substrings that, found in the PDF's own Creator/Producer metadata,
    # identify this program outright.
    creator_markers: tuple = ()
    # Substrings printed on the page that name the program.
    page_markers: tuple = ()
    # Header tokens distinctive enough to imply this format.
    signature_header_tokens: tuple = ()
    # A regex matching this format's subtotal phrasing.
    subtotal_signature: Optional[Pattern] = None
    # Any other printed evidence worth scoring, as
    # (human-readable name, compiled regex, weight).
    extra_signals: tuple = ()


# ---------------------------------------------------------------------
# Xactimate -- the gold-standard sheet. Every value here was lifted
# verbatim from the module named in the comment beside it.
# ---------------------------------------------------------------------

_XACT_NOTE_TRIGGERS = re.compile(
    r"^(Auto Calculated|Options:|This line item|Note:|Allow|Allowing|Received|"
    r"Invoice|Waste is|Component|STEEP AND|ROOF |R\d{3}\.|IRC |IBC |•|\*\*\*|"
    r"Per IRC|Per state|The roof|The appropriate|Lowes|Three nails|Cost of|OSH|"
    r"29 CFR|1926\.|1910\.|Louisiana|Each construction|Willful|Failure|Falsifying|"
    r"Criminal|No visible|High Wind|This loss|3-tab|Class [A-Z]|Asphalt shingles|"
    r"Roof decks|Roof coverings|Roof replacement|Flashings shall|Valley linings|"
    r"For (open|closed) valleys|R905|R903|R908|1503\.|1504\.|1609\.)",
    re.IGNORECASE,
)

XACTIMATE = Profile(
    key="xactimate",
    label="Xactimate",
    unit_tokens=XACTIMATE_UNIT_TOKENS,                        # units.py
    item_number_re=re.compile(r"^\*?\s*(\d{1,3})([a-z])?\.\s+(\S.*)$"),   # line_items.py
    totals_re=re.compile(r"^(?:[A-Za-z][A-Za-z0-9'&()/., -]*?\s+)?(Totals?):\s*(.*)$"),  # line_items.py
    continued_re=re.compile(r"^CONTINUED\s*-\s*(.+)$", re.IGNORECASE),
    page_furniture_re=re.compile(r"Page:\s*\d+|^\d{1,3}$"),         # line_items.py
    note_triggers=_XACT_NOTE_TRIGGERS,                              # line_items.py
    max_continuation_words=2,
    column_strategy="header",
    header_token_to_field={                                          # schema.py
        "PRICE": "unit_price",
        "TAX": "tax",
        "O&P": "overhead_profit",
        # State Farm prints the overhead & profit column as "GCO&P"
        # (General Contractor O&P) under the same QUANTITY/UNIT PRICE
        # layout as Xactimate's other columns.
        "GCO&P": "overhead_profit",
        "RCV": "rcv",
        "AGE/LIFE": "age_life",
        "COND.": "condition",
        "COND": "condition",
        "DEP%": "depreciation_pct",
        "DEPREC.": "depreciation",
        "DEPREC": "depreciation",
        # USAA's Xactimate export spells the depreciation column out in
        # full ("... RCV DEPRECIATION ACV") instead of the usual DEPREC.
        "DEPRECIATION": "depreciation",
        "ACV": "acv",
        # Xactimate's contractor-facing export prints three ACTION cost
        # columns instead of one price column, and a tax-inclusive total.
        # See _parse_action_tail() in line_items.py for how these are read.
        "RESET": "action_reset",
        "REMOVE": "action_remove",
        "REPLACE": "action_replace",
        "*TOTAL": "rcv",
        "TOTAL": "rcv",
    },
    ignored_header_tokens=frozenset({"DESCRIPTION", "QUANTITY", "QTY", "UNIT"}),
    insert_missing_unit_price=True,
    default_schema=("unit_price", "rcv", "acv"),
    boilerplate_page_markers=("GUIDE_EXAMPLE",),                     # extract.py
    carrier_tax_included=False,
    creator_markers=("xactimate", "xactware", "xactanalysis"),
    page_markers=("xactimate", "xactware"),
    signature_header_tokens=("RCV", "AGE/LIFE", "DEP%", "DEPREC.", "ACV", "O&P"),
    subtotal_signature=re.compile(r"^Totals?:\s", re.IGNORECASE),
    extra_signals=(
        # The price-list index ("Price List: TXHO8X_AUG24") is an
        # Xactimate-family habit and appears on all three real fixtures.
        # It also names the state -- see fingerprint.jurisdiction_state.
        ("price-list index", re.compile(r"Price List:\s*([A-Z]{2}[A-Z0-9]{3,}_[A-Z]{3}\d{2})"), 15),
    ),
)


# ---------------------------------------------------------------------
# Generic -- for programs nobody has taught this parser about.
#
# It names no columns, because it can't. Instead it finds the
# quantity/unit anchor (format-blind, see tokens.find_qty_and_unit) and
# then SOLVES for the unit-price and line-total columns arithmetically.
# See generic_reader.py.
# ---------------------------------------------------------------------

GENERIC = Profile(
    key="generic",
    label="Unrecognised format (general reader)",
    unit_tokens=GENERIC_UNIT_TOKENS,
    # Permissive: matches "14. Description", "10a. Description" AND
    # Symbility's action-prefixed "1 Remove - Description". Rows with no
    # number at all are started by the quantity/unit anchor instead.
    item_number_re=re.compile(r"^(\d{1,3})([a-z])?[.)]?\s+(\S.*)$"),
    totals_re=re.compile(
        r"^(?:.*?[-–]\s*)?(Totals?|Subtotals?|Sub-totals?)\b[:\s]\s*(.*)$", re.IGNORECASE
    ),
    continued_re=re.compile(r"^CONTINUED\s*-\s*(.+)$", re.IGNORECASE),
    page_furniture_re=re.compile(r"Page:?\s*\d+|^\d{1,3}$|^Page \d+ of \d+$", re.IGNORECASE),
    note_triggers=None,
    max_continuation_words=2,
    column_strategy="arithmetic",
    insert_missing_unit_price=False,
    boilerplate_page_markers=(),
    carrier_tax_included=False,
)


# ---------------------------------------------------------------------
# Symbility / Cotality -- SKELETON ONLY, not yet trusted.
#
# Built from published format descriptions and from one real Liberty
# Mutual estimate seen second-hand, NOT from a real PDF parsed here. It is
# deliberately NOT in the registry below: until a genuine Symbility PDF is
# checked in as a fixture with its own golden snapshot, the generic reader
# is the honest answer for these documents. Selecting a half-verified
# sheet would be exactly the kind of confident guess this parser refuses
# to make everywhere else.
# ---------------------------------------------------------------------

SYMBILITY_DRAFT = Profile(
    key="symbility",
    label="Symbility / Cotality",
    unit_tokens=GENERIC_UNIT_TOKENS,
    item_number_re=re.compile(
        r"^(\d{1,3})()\s+((?:Remove|Replace|Detach|Reset|Install|Seal|Paint|Clean|Tear)\b.*)$",
        re.IGNORECASE,
    ),
    totals_re=re.compile(r"^(?:.*?[-–]\s*)?(Subtotals?|Totals?)\b.*?[:\s]\s*(.*)$", re.IGNORECASE),
    continued_re=None,
    page_furniture_re=re.compile(r"Page:?\s*\d+|^\d{1,3}$", re.IGNORECASE),
    note_triggers=None,
    column_strategy="arithmetic",
    carrier_tax_included=True,  # Symbility computes tax into its own total
    creator_markers=("symbility", "cotality", "corelogic"),
    # "data driven usdc" is here because the pricing-database line is the
    # only place a real Liberty Mutual/Cotality estimate names its engine
    # -- and on that document the word "Cotality" itself extracts as
    # "C otality", split by the two-column layout it sits in. Matching the
    # database name instead survives that.
    page_markers=("symbility", "cotality", "corelogic", "data driven usdc"),
    signature_header_tokens=("PRICE PER", "TOTAL COST", "TOTAL O&P", "TOTAL TAXES"),
    subtotal_signature=re.compile(r"-\s*Subtotal\s*\(\d+\s+items?\)", re.IGNORECASE),
    extra_signals=(
        ("pricing-database line", re.compile(r"Pricing Database:", re.IGNORECASE), 12),
    ),
)


# Only sheets validated against a real checked-in fixture belong here.
REGISTRY = {
    XACTIMATE.key: XACTIMATE,
    GENERIC.key: GENERIC,
}

# Sheets that may be used for IDENTIFYING a document (so the app can tell
# the contractor "this looks like Symbility") but not for parsing it.
IDENTIFY_ONLY = {
    SYMBILITY_DRAFT.key: SYMBILITY_DRAFT,
}

DEFAULT = XACTIMATE


def get(key):
    return REGISTRY.get(key, GENERIC)
