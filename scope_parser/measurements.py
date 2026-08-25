"""Pulls the roof/room measurement blocks out (surface area, squares,
perimeter, ridge/hip length, walls/ceiling/floor areas, etc.) -- the
numbers the project's parsing-problems doc calls out as easy to lose if a
parser strips CAD sketch pages wholesale. See problem #1.

Deliberately best-effort: these numbers are useful context for a
contractor reviewing a claim, but unlike line items they are not money, so
a slightly truncated label is a cosmetic issue, not a financial one -- it
does not get the same anti-guessing treatment as line_items.py.
"""
import re

from .tokens import find_qty_and_unit, split_fused_tokens

_VALUE_RE = re.compile(r"^[\d,]+\.\d{1,2}$")
_TRAILING_PREPOSITION = re.compile(r"\b(of|and|to|the|for)$", re.IGNORECASE)


def is_measurement_line(line: str) -> bool:
    """A line is a measurement block line if it has no line-item-style
    quantity/unit data row on it, but does contain at least one
    "NUMBER Label Words" pair.
    """
    tokens = split_fused_tokens(line.split())
    if len(tokens) < 2:
        return False
    if find_qty_and_unit(tokens) is not None:
        return False  # this is a priced line item row, not a measurement
    return _extract_pairs(tokens) != []


# "Roof area: 2,799.23 SF", "Building perimeter (ground): 1.79 LF",
# "Squares: 28.0 SQ" -- a labelled measurement, as printed by Symbility
# and by several carriers' plan/elevation headers.
_LABELLED_MEASUREMENT_RE = re.compile(
    r"[A-Za-z][A-Za-z ./()-]*:\s*\$?[\d,]+(?:\.\d+)?", re.IGNORECASE
)


def has_labelled_measurement(line: str) -> bool:
    """True for a "<Label>: <number>" measurement line.

    Deliberately SEPARATE from is_measurement_line(), which asks a
    different question and is relied on by the Xactimate path. This one
    exists because Symbility prints plan measurements in a shape that
    looks exactly like a priced row to a quantity/unit anchor: "Roof area:
    2,799.23 SF Squares: 28.0 SQ Soffit: 690.70 SF" has a number, a unit,
    and more numbers after it. Read as a line item it invented a $690.70
    row on a real claim and threw that section's subtotal out by exactly
    that amount.

    A real priced row never carries a "Label:" before its figures, which
    is what makes this safe to use as a veto.
    """
    return bool(_LABELLED_MEASUREMENT_RE.search(line))


def _extract_pairs(tokens):
    pairs = []
    i = 0
    n = len(tokens)
    while i < n:
        if _VALUE_RE.match(tokens[i]):
            value = tokens[i]
            i += 1
            label_tokens = []
            while i < n and not _VALUE_RE.match(tokens[i]):
                label_tokens.append(tokens[i])
                i += 1
            if label_tokens:
                pairs.append((value, " ".join(label_tokens)))
        else:
            i += 1
    return pairs


def extract_measurements(lines):
    """lines: iterable of raw text lines (already noise-filtered).

    Tracks the nearest short title-like line above each measurement block
    as a best-guess section label -- good enough to group measurements by
    room/roof-plane for display, but not load-bearing the way section
    names are for line items (see totals.py, which only cross-checks
    money).

    Returns a list of (section, label, value, unit) tuples. `unit` is a
    guess (SF/LF/SY/SQ) pulled off the front of the label when present.
    """
    results = []
    current_title = "Unknown"
    pending = None  # (section, value, label) waiting for a possible
    # continuation label on the next line, e.g. "Exterior Perimeter of" /
    # "Walls" split across two lines in the "Grand Total Areas" block.
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not is_measurement_line(line):
            if pending and not _VALUE_RE.match(line.split()[0]) and len(line.split()) <= 3:
                section, value, label = pending
                label = f"{label} {line}".strip()
                results[-1] = (section,) + _split_unit(label, value)
                pending = None
                continue
            pending = None
            words = line.split()
            if 1 <= len(words) <= 6 and not line.endswith((".", ":")) and line[0].isalpha():
                current_title = line
            continue
        section = current_title
        pairs = _extract_pairs(line.split())
        for value, label in pairs:
            results.append((section,) + _split_unit(label, value))
        if pairs and _TRAILING_PREPOSITION.search(pairs[-1][1]):
            pending = (section, pairs[-1][0], pairs[-1][1])
        else:
            pending = None
    return results


_UNIT_PREFIXES = ("SF", "LF", "SY", "SQ", "CF", "CY")


def _split_unit(label, raw_value):
    words = label.split()
    unit = ""
    if words and words[0] in _UNIT_PREFIXES:
        unit = words[0]
        label = " ".join(words[1:])
    value = float(raw_value.replace(",", ""))
    return label, value, unit
