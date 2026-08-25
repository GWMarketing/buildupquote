"""Shared low-level token helpers used by both line_items.py and
measurements.py. Split out on its own so neither module has to import the
other (they both need "is this a quantity/unit pair" logic).
"""
import re
from collections import namedtuple
from functools import lru_cache

from .units import UNIT_TOKENS

QTY_RE = re.compile(r"^-?\d[\d,]*(\.\d+)?$")
MONEY_RE = re.compile(r"[<(]?-?\$?[\d,]+\.\d{2}[)>]?")

# Some carrier PDFs' text extraction drops the space between a quantity and
# its unit -- "35.01SQ" instead of "35.01 SQ" -- because the source PDF's
# glyphs are positioned close enough together that no real space character
# exists to extract; it's a font/export quirk of that specific document, not
# something a parser choice can avoid. Sort units longest-first so e.g. "SQ"
# doesn't shadow a unit that happens to end the same way.
@lru_cache(maxsize=8)
def _fused_re(unit_tokens):
    # Sort units longest-first so e.g. "SQ" doesn't shadow a unit that
    # happens to end the same way. Cached per vocabulary so the Xactimate
    # set and the generic set each compile exactly once.
    return re.compile(
        r"^(-?\d[\d,]*(?:\.\d+)?)(" + "|".join(sorted(unit_tokens, key=len, reverse=True)) + r")$"
    )


def split_fused_tokens(tokens, unit_tokens=UNIT_TOKENS):
    """Split any "<quantity><unit>" token with no space between them (e.g.
    "35.01SQ") into two separate tokens ("35.01", "SQ"). Everything else
    passes through unchanged. Call this once, right after `line.split()`,
    before doing any quantity/unit-position logic -- see find_qty_and_unit.

    `unit_tokens` defaults to the Xactimate vocabulary, so every existing
    caller behaves exactly as it did before this parameter existed. The
    generic reader passes its own wider set; see units.py for why the two
    are kept apart.
    """
    pattern = _fused_re(frozenset(unit_tokens))
    result = []
    for tok in tokens:
        m = pattern.match(tok)
        if m:
            result.append(m.group(1))
            result.append(m.group(2))
        else:
            result.append(tok)
    return result


def find_qty_and_unit(tokens, unit_tokens=UNIT_TOKENS):
    """Return the index of a quantity token immediately followed by a known
    unit token (e.g. "12.57", "SQ"), or None if no such pair exists.

    This is the anchor the whole line-item parser is built around: once we
    know where quantity+unit sit on a line, everything before is
    description and everything after is the data columns for that row.

    It is also the one piece of this parser that is already completely
    format-blind -- every estimating program on the market prints a
    quantity next to a unit -- which is why the generic reader is built on
    top of it rather than on anything Xactimate-specific.

    Verified against Symbility-shaped rows, which print their unit price as
    "$3.99 / LF": the anchor correctly takes the FIRST quantity/unit pair
    ("140.00 LF"), and a row with no printed quantity finds no anchor at
    all rather than mistaking the price's "/ LF" for one. The leftover
    "/ LF" tokens land in the tail, which is why strip_tail_noise() below
    exists.
    """
    for i in range(len(tokens) - 1):
        if QTY_RE.match(tokens[i]) and tokens[i + 1] in unit_tokens:
            return i
    return None


# A money-shaped token, with or without a currency symbol: "$1.63",
# "1,234.56". Used by the second anchor form below.
MONEYISH_RE = re.compile(r"^\$?-?[\d,]+\.\d{2}$")

# A quantity printed in brackets right after the measured one, e.g.
# "22.21 (22.33)" -- Symbility's bundle-rounded ordering quantity.
BRACKETED_QTY_RE = re.compile(r"^\(([\d,]+(?:\.\d+)?)\)$")

Anchor = namedtuple("Anchor", "quantity_index unit_index tail_start priced_quantity_index")


def find_anchor(tokens, unit_tokens=UNIT_TOKENS):
    """Find a row's quantity, unit and where its data columns start.

    Two printed layouts exist in the wild and they are NOT interchangeable:

      A. quantity then unit           "35.01 SQ 58.24 0.00 2,039.08"
         -- Xactimate and most carrier estimates.

      B. quantity, unit price, unit   "6.00 $1.63 LF $0.00 $9.78"
         -- Symbility/Cotality, which prints the unit as a per-unit
         qualifier on the PRICE ("$1.63 per LF") rather than on the
         quantity.

    Form B is why a real Liberty Mutual estimate produced ZERO line items
    before this existed: form A never matches it, because the quantity is
    followed by a price, not a unit.

    Form B also allows a bracketed second quantity between the two --
    "22.21 (22.33) $107.81 SQ". Symbility prints the ordered quantity in
    brackets after the measured one when bundle rounding applies, and
    prices the line off the BRACKETED figure: 22.33 x $107.81 + $198.61
    tax = $2,606.01 exactly, where the measured 22.21 is $12.96 short.
    Ignoring it lost a real $2,606.01 line item on a real Liberty Mutual
    claim; that one line was the whole difference between its roof
    subtotal reconciling and not.

    Returns `Anchor(quantity_index, unit_index, tail_start,
    priced_quantity_index)` or None -- `priced_quantity_index` is the
    bracketed figure's position when there is one, else None. Scans left
    to right and takes the earliest anchor, preferring form A at the same
    position, so a document that satisfies both is read the ordinary way.

    Note that form B's tail STARTS at the price, with the unit token left
    inside it for strip_tail_noise() to remove, so the price keeps its
    place as the row's first data column in both forms.
    """
    for i in range(len(tokens) - 1):
        if not QTY_RE.match(tokens[i]):
            continue
        if tokens[i + 1] in unit_tokens:
            return Anchor(i, i + 1, i + 2, None)
        if (
            i + 2 < len(tokens)
            and MONEYISH_RE.match(tokens[i + 1])
            and tokens[i + 2] in unit_tokens
        ):
            return Anchor(i, i + 2, i + 1, None)
        if (
            i + 3 < len(tokens)
            and BRACKETED_QTY_RE.match(tokens[i + 1])
            and MONEYISH_RE.match(tokens[i + 2])
            and tokens[i + 3] in unit_tokens
        ):
            return Anchor(i, i + 3, i + 2, i + 1)
    return None


def strip_tail_noise(tokens, unit_tokens=UNIT_TOKENS):
    """Drop separators and repeated unit tokens from a row's trailing data
    columns, so column POSITIONS line up before anything reads them.

    Needed for per-unit price notation like Symbility's "$3.99 / LF", which
    leaves "/" and "LF" sitting between two real figures. Only used by the
    generic reader -- the header-driven path knows its column count exactly
    and must not have tokens quietly removed underneath it.
    """
    return [t for t in tokens if t not in ("/", "|", "@") and t.upper() not in unit_tokens]


def parse_number(token):
    """Parse a plain numeric column (unit price, tax, O&P, RCV, ACV).

    Returns None (rather than raising) on anything unexpected so callers
    can flag the row for manual review instead of crashing or silently
    mis-parsing a financial figure.
    """
    if token is None:
        return None
    t = token.strip().replace("$", "").replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_depreciation(token):
    """Parse a depreciation column, which carries its own meaning in the
    wrapping punctuation: (123.45) = recoverable, <123.45> = non-recoverable
    (per the project's legal notes on deductible/depreciation handling).

    Returns (magnitude, recoverable) where recoverable is True/False/None
    (None = wrapping wasn't present so we can't say).
    """
    if token is None:
        return None, None
    t = token.strip()
    if t.startswith("(") and t.endswith(")"):
        val = parse_number(t[1:-1])
        return val, (True if val is not None else None)
    if t.startswith("<") and t.endswith(">"):
        val = parse_number(t[1:-1])
        return val, (False if val is not None else None)
    return parse_number(t), None
