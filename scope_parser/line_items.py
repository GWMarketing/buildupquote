"""The core line-item state machine.

Handles two of the traps called out in the project's "Known parsing
problems" doc directly:

  #2 Multi-line descriptions / line-wrap collisions -- Xactimate sometimes
     prints the data row (qty/price/RCV/...) in the middle of a
     description, with the last word or two of the description trailing
     on the next line (e.g. "...comp. shingle rfg. - w/ <DATA ROW>\\nfelt").
     We tell that apart from a genuine trailing note (a full sentence,
     multiple words, ending in punctuation) with a short heuristic in
     `_looks_like_continuation`.

  #5 O&P / column layout varies by section -- handled by asking schema.py
     for the column order every time we see a new header line, rather
     than assuming one fixed layout for the whole document.

This module only understands text lines; noise filtering and PDF
extraction happen elsewhere in the pipeline.
"""
import re

from . import schema
from .codes import mentions_code_citation
from .measurements import is_measurement_line
from .models import LineItem
from .profiles import XACTIMATE
from .tokens import MONEY_RE, find_qty_and_unit, parse_depreciation, parse_number, split_fused_tokens

# These four now live on the rule sheet (profiles.XACTIMATE) and are kept
# here only so existing imports keep working. The functions read the sheet.
_ITEM_NUMBER_RE = re.compile(r"^(\d{1,3})([a-z])?\.\s+(\S.*)$")
_TOTALS_RE = re.compile(r"^(Totals?):\s*(.*)$")
_CONTINUED_RE = re.compile(r"^CONTINUED\s*-\s*(.+)$", re.IGNORECASE)
_PAGE_FURNITURE_RE = re.compile(r"Page:\s*\d+|^\d{1,3}$")

_NOTE_TRIGGERS = re.compile(
    r"^(Auto Calculated|Options:|This line item|Note:|Allow|Allowing|Received|"
    r"Invoice|Waste is|Component|STEEP AND|ROOF |R\d{3}\.|IRC |IBC |•|\*\*\*|"
    r"Per IRC|Per state|The roof|The appropriate|Lowes|Three nails|Cost of|OSH|"
    r"29 CFR|1926\.|1910\.|Louisiana|Each construction|Willful|Failure|Falsifying|"
    r"Criminal|No visible|High Wind|This loss|3-tab|Class [A-Z]|Asphalt shingles|"
    r"Roof decks|Roof coverings|Roof replacement|Flashings shall|Valley linings|"
    r"For (open|closed) valleys|R905|R903|R908|1503\.|1504\.|1609\.)",
    re.IGNORECASE,
)


def parse_item_number(line, profile=XACTIMATE):
    if profile.item_number_re is None:
        return None
    m = profile.item_number_re.match(line)
    if not m:
        return None
    number = m.group(1) + (m.group(2) or "")
    return number, m.group(3)


def _looks_like_continuation(line, profile=XACTIMATE):
    words = line.split()
    if len(words) > profile.max_continuation_words:
        return False
    if line.rstrip().endswith((".", "!", "?", ":")):
        return False
    if profile.note_triggers is not None and profile.note_triggers.match(line):
        return False
    return True


def _title_candidate(line):
    """Pull a short, plausible section/room title off the front of `line`,
    stopping at the first token that contains a digit.

    Real section-title lines are sometimes glued to sketch dimension text
    by the PDF extractor (e.g. "Bathroom 9' 4\"" or "Left Elevation Formula
    Elevation 58' 6\" x 0\" x 11' 10\""). Taking just the leading
    alphabetic run gives a usable label ("Bathroom", "Left Elevation
    Formula Elevation") instead of either the whole garbled line or
    nothing at all.
    """
    if re.match(r"^(Door|Window|Missing Wall)\b", line, re.IGNORECASE):
        return None  # an opening declaration, not a room/section title
    lead = []
    for word in line.split():
        if not word[0].isalpha():
            break  # e.g. "58'", "6\"", "(2)" -- a dimension, not a label word
        lead.append(word)
    if lead and 1 <= len(lead) <= 6:
        return " ".join(lead)
    return None


def _split_leading_label(text):
    """For a "Totals: <label> <numbers...>" line, pull off <label>."""
    label_tokens = []
    for token in text.split():
        if MONEY_RE.fullmatch(token):
            break
        label_tokens.append(token)
    return " ".join(label_tokens)


def _consume_data_row(text, item, profile=XACTIMATE):
    """If `text` contains a quantity/unit pair, split it into a description
    prefix (appended to item) and the trailing data tokens. Returns True if
    a data row was found.

    The pair only counts as a real data-row anchor when something actually
    follows it: a description that merely MENTIONS a measurement ("Saddle or
    cricket - up to 25 SF", "R&R Window screen, 10 - 16 SF") has no data
    after the unit, while a genuine row always does -- its price/tax/RCV/
    ... columns begin with a number. Without this guard the unit inside such
    a description was read as the row's quantity and the real data row on
    the next line was swallowed as a note (real PDF, 2026-08-27).
    """
    tokens = split_fused_tokens(text.split(), profile.unit_tokens)
    idx = find_qty_and_unit(tokens, profile.unit_tokens)
    if idx is None:
        return False
    tail = tokens[idx + 2:]
    if not tail or parse_number(tail[0]) is None:
        return False
    prefix = " ".join(tokens[:idx])
    if prefix:
        item["desc_parts"].append(prefix)
    item["quantity"] = parse_number(tokens[idx])
    item["unit"] = tokens[idx + 1]
    item["tail_tokens"] = tail
    return True


# The most sales tax a line could plausibly carry. Used only as a
# sanity bound on the gap between a tax-inclusive printed total and the
# pre-tax cost -- not to compute tax, which is never guessed at.
_MAX_PLAUSIBLE_TAX_RATE = 0.15


def _parse_action_tail(tokens, quantity, field_schema):
    """Read a row from Xactimate's contractor-facing export, which prints
    RESET / REMOVE / REPLACE cost columns and a tax-inclusive *TOTAL.

    Two things make this different from every other layout here:

    1. **Empty cost cells don't print at all.** A row with no RESET cost
       extracts as two numbers plus the total, not three plus the total,
       so the columns cannot be mapped by position. What CAN be relied on
       is that the last number is the line total and the ones before it
       are costs. Their SUM is the row's real unit price -- an R&R line
       costing $1.77/SF to tear out and $2.96/SF to install is a $4.73/SF
       line, and 172.50 SF of it is $815.93 of work.

    2. **The total already includes sales tax** ("* Price is inclusive of
       sales tax paid at point of purchase"), so quantity x cost does NOT
       equal the printed total on any row carrying materials. The gap IS
       the tax, and it is recorded as tax rather than folded into the
       price -- which keeps the contractor's margin applying to the cost
       of the work, exactly as it does on every other format.

    Returns (values, problems).
    """
    values = {}
    numbers = [parse_number(t) for t in tokens]
    if len(numbers) < 2:
        return values, ["expected at least a cost and a total on this row"]
    if any(n is None for n in numbers):
        bad = [t for t, n in zip(tokens, numbers) if n is None]
        return values, [f"expected only numbers in the cost columns, got {bad}"]

    total = numbers[-1]
    unit_price = round(sum(numbers[:-1]), 2)
    values["unit_price"] = unit_price
    values["rcv"] = total
    for field, number in zip([f for f in field_schema if f in schema.ACTION_FIELDS], numbers[:-1]):
        values[field] = number

    if quantity is None:
        return values, ["no quantity printed, so this row's total could not be checked"]

    base = quantity * unit_price
    gap = round(total - base, 2)
    allowance = max(0.02, abs(base) * _MAX_PLAUSIBLE_TAX_RATE)
    if gap < -0.02 or gap > allowance:
        return values, [
            f"quantity x cost is {base:,.2f} but the printed total is {total:,.2f}"
        ]
    if gap > 0.005:
        values["tax"] = gap
    return values, []


def _parse_tail(tokens, field_schema):
    """Walk `tokens` consuming one field at a time per `field_schema`
    (the column order for this section, e.g. from schema.parse_header).

    Returns (values_dict, leftover_tokens, problems). `problems` is a list
    of human-readable strings describing anything that didn't line up --
    the caller uses a non-empty list to set needs_review rather than
    guessing at a number.
    """
    values = {}
    problems = []
    idx = 0
    n = len(tokens)
    for field in field_schema:
        if idx >= n:
            problems.append(f"ran out of columns before '{field}'")
            break
        tok = tokens[idx]
        if field in ("unit_price", "tax", "overhead_profit", "rcv", "acv"):
            val = parse_number(tok)
            if val is None:
                problems.append(f"expected a number for '{field}', got '{tok}'")
                break
            values[field] = val
            idx += 1
        elif field == "depreciation":
            val, recoverable = parse_depreciation(tok)
            if val is None:
                problems.append(f"expected a depreciation amount, got '{tok}'")
                break
            values["depreciation"] = val
            values["depreciation_recoverable"] = recoverable
            idx += 1
        elif field == "age_life":
            # A bare "NA" is a real, valid value: the carrier printed no
            # age/life for this line (removal-only rows, dumpster loads,
            # etc.). It parses as unknown rather than flagging the row.
            m = re.match(r"^(?:NA|(\d+)/(\d+|NA))$", tok)
            if not m:
                problems.append(f"expected an age/life value, got '{tok}'")
                break
            if tok.upper() == "NA":
                values["age"], values["life"] = None, None
            else:
                values["age"], values["life"] = m.group(1), m.group(2)
            idx += 1
            if idx < n and re.match(r"^yrs\.?$", tokens[idx], re.IGNORECASE):
                idx += 1
        elif field == "condition":
            cond = tok
            idx += 1
            if cond in ("Below", "Above") and idx < n and tokens[idx].rstrip(".") == "Average":
                cond = f"{cond} {tokens[idx]}"
                idx += 1
            values["condition"] = cond
        elif field == "depreciation_pct":
            if not re.match(r"^(NA|\d+(\.\d+)?%)$", tok):
                problems.append(f"expected a depreciation %%, got '{tok}'")
                break
            values["depreciation_pct"] = tok
            idx += 1
            if idx < n and tokens[idx].upper() in ("[M]",):
                idx += 1
        else:
            values[field] = tok
            idx += 1
    leftover = tokens[idx:]
    if leftover:
        problems.append(f"unused trailing tokens: {leftover}")
    return values, leftover, problems


def _build_line_item(item):
    description = " ".join(p for p in item["desc_parts"] if p).strip()
    description = re.sub(r"\s+", " ", description)
    if any(f in schema.ACTION_FIELDS for f in item["schema_used"]):
        values, problems = _parse_action_tail(
            item["tail_tokens"], item["quantity"], item["schema_used"]
        )
    else:
        values, leftover, problems = _parse_tail(item["tail_tokens"], item["schema_used"])
    li = LineItem(
        number=item["number"],
        description=description,
        quantity=item["quantity"],
        unit=item["unit"],
        section=item["section"],
        notes=list(item["notes"]),
        unit_price=values.get("unit_price"),
        tax=values.get("tax"),
        overhead_profit=values.get("overhead_profit"),
        rcv=values.get("rcv"),
        age=values.get("age"),
        life=values.get("life"),
        condition=values.get("condition"),
        depreciation_pct=values.get("depreciation_pct"),
        depreciation=values.get("depreciation"),
        depreciation_recoverable=values.get("depreciation_recoverable"),
        acv=values.get("acv"),
        raw_tail_tokens=item["tail_tokens"],
        code_related=mentions_code_citation(description, *item["notes"]),
    )
    if problems:
        li.needs_review = True
        li.review_reason = "; ".join(problems)
    return li


def parse_items_and_sections(lines, profile=XACTIMATE):
    """Main entry point. Returns (line_items, section_totals, warnings).

    section_totals is a list of (section_name, [printed numbers]) pulled
    off "Totals:"/"Total:" lines, for the cross-check in totals.py.

    `profile` defaults to the Xactimate rule sheet, so every existing
    caller behaves exactly as it did before rule sheets existed -- which
    the golden-snapshot lock in tests/ checks on every run.
    """
    items = []
    section_totals = []  # (label, printed_numbers, rcv_sum_since_last_totals_line)
    warnings = []

    current_section = "Unknown"
    pending_title = None
    current_schema = list(profile.default_schema or schema.DEFAULT_SCHEMA)
    item = None
    since_last_total = 0.0

    def finalize():
        nonlocal item, since_last_total
        if item is not None and item.get("tail_tokens") is not None:
            li = _build_line_item(item)
            items.append(li)
            if li.rcv is not None:
                since_last_total += li.rcv
        item = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if schema.is_header_line(stripped, profile):
            finalize()
            current_schema = schema.parse_header(stripped, profile)
            if pending_title:
                current_section = pending_title
                pending_title = None
            continue

        m_total = profile.totals_re.match(stripped)
        if m_total:
            finalize()
            remainder = m_total.group(2)
            label = _split_leading_label(remainder) or current_section
            numbers = [parse_number(n) for n in MONEY_RE.findall(remainder)]
            section_totals.append((label, [n for n in numbers if n is not None], round(since_last_total, 2)))
            since_last_total = 0.0
            continue

        m_continued = profile.continued_re.match(stripped) if profile.continued_re else None
        if m_continued:
            # A repeated page-break header ("CONTINUED - Roof1") is an
            # unambiguous signal of which section we're still in -- use it
            # even if stray letterhead/page-number lines came in between.
            # Checked before the active-item branches below so it can't
            # get swallowed as a "note" on whatever item is still open.
            pending_title = m_continued.group(1).strip()
            continue

        item_match = parse_item_number(stripped, profile)
        if item_match:
            finalize()
            number, rest = item_match
            item = {
                "number": number, "desc_parts": [], "tail_tokens": None,
                "quantity": None, "unit": None, "notes": [],
                "section": current_section, "schema_used": current_schema,
            }
            if not _consume_data_row(rest, item, profile):
                item["desc_parts"].append(rest)
            continue

        if is_measurement_line(stripped):
            continue

        if item is not None and item["tail_tokens"] is None:
            if not _consume_data_row(stripped, item, profile):
                item["desc_parts"].append(stripped)
            continue

        if item is not None and item["tail_tokens"] is not None:
            # Page furniture (a lone page number, "Page: N") must be checked
            # BEFORE the continuation heuristic, not after: a bare page
            # number like "3" is short and has no terminal punctuation, so
            # it used to satisfy _looks_like_continuation and get appended
            # straight into the item's description -- e.g. a real bug
            # found via a genuine carrier PDF: "Roofing felt - 15 lb. 3".
            if profile.page_furniture_re.search(stripped):
                pass
            elif _looks_like_continuation(stripped, profile):
                item["desc_parts"].append(stripped)
            else:
                item["notes"].append(stripped)
            continue

        # No active item and not a header/total/measurement/item-start --
        # a section title candidate, a disclaimer sentence, or similar
        # free text. Section titles are short and title-cased; remember
        # the most recent such line as the name for the *next* section
        # header we see.
        candidate = _title_candidate(stripped)
        if candidate:
            pending_title = candidate

    finalize()
    return items, section_totals, warnings
