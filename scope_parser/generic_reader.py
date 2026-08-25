"""Reads a priced estimate from a program this parser has never been
taught about.

Deliberately a SEPARATE scanner from line_items.py rather than more
branches inside it. The two use genuinely different strategies for the one
thing they can't share -- how to identify the columns after the
quantity -- and keeping them apart means work on this reader can never
alter the Xactimate path. Everything either of them could share, they do:
the quantity/unit anchor (tokens.py), noise filtering, measurements, the
totals-consistency check, claim flags and metadata are all common code.

The strategy:

  1. Find the quantity/unit anchor on each row. This is format-blind --
     every estimating program prints a quantity next to a unit.
  2. Everything left of the anchor is description; everything right of it
     is columns of figures whose names we don't know.
  3. At each subtotal line, solve the whole section at once for which
     column is the unit price and which is the line total
     (generic_columns.py). One row can balance by luck; a section can't.
  4. Rows that don't fit the section's solution are flagged, not guessed.
"""
import re

from . import generic_columns
from .codes import mentions_code_citation
from .line_items import _split_leading_label, _title_candidate
from .measurements import has_labelled_measurement, is_measurement_line
from .models import LineItem
from .profiles import GENERIC
from .tokens import (
    MONEY_RE,
    find_anchor,
    parse_number,
    split_fused_tokens,
    strip_tail_noise,
)


def _is_priced_row(record):
    """Does this look like a priced scope row rather than a measurement?"""
    description = " ".join(record["desc_parts"]).strip()
    if not description:
        return False
    values = [v for v in generic_columns.numeric_tail(record["tail_tokens"] or []) if v is not None]
    return len(values) >= 2


def _new_record(number, section):
    return {
        "number": number,
        "desc_parts": [],
        "notes": [],
        "quantity": None,
        "unit": None,
        "tail_tokens": None,
        "section": section,
    }


def _consume(text, record, profile):
    tokens = split_fused_tokens(text.split(), profile.unit_tokens)
    anchor = find_anchor(tokens, profile.unit_tokens)
    if anchor is None:
        return False
    prefix = " ".join(tokens[:anchor.quantity_index])
    if prefix:
        record["desc_parts"].append(prefix)
    record["quantity"] = parse_number(tokens[anchor.quantity_index])
    if anchor.priced_quantity_index is not None:
        # The bracketed figure is the one the line is actually priced on.
        # Keep the measured quantity visible in the notes rather than
        # silently discarding it.
        measured = tokens[anchor.quantity_index]
        record["quantity"] = parse_number(tokens[anchor.priced_quantity_index].strip("()"))
        record["notes"].append(
            f"Measured quantity {measured}; priced on the ordered quantity "
            f"{tokens[anchor.priced_quantity_index].strip('()')} (bundle rounding)."
        )
    record["unit"] = tokens[anchor.unit_index]
    # Strip separators and repeated units ("$3.99 / LF", or Symbility's
    # unit sitting between the price and the rest of the row) so that
    # column POSITIONS line up across rows before any arithmetic reads
    # them.
    record["tail_tokens"] = strip_tail_noise(tokens[anchor.tail_start:], profile.unit_tokens)
    return True


def _rows_of(records):
    return [
        (r["quantity"], generic_columns.numeric_tail(r["tail_tokens"] or []))
        for r in records
    ]


def _explains(solution, rows):
    """How many of `rows` this solution actually balances."""
    if solution is None:
        return 0
    hits = 0
    for qty, values in rows:
        if qty in (None, 0):
            continue
        if solution.price_index < len(values) and solution.total_index < len(values):
            ok, _ = generic_columns._row_matches(
                qty, values, solution.price_index, solution.total_index
            )
            hits += 1 if ok else 0
    return hits


def _finish_section(records, warnings, document_solution=None):
    """Turn one section's records into LineItems.

    Column layout is usually constant across a whole document, so the
    document-wide solution is the default and a section only overrides it
    when the section's own arithmetic explains MORE of its rows. Without
    this, a short section -- three rows in "Debris Removal" -- would have
    too little evidence to solve on its own and would be flagged even
    though the rest of the document already proved the layout.
    """
    if not records:
        return []

    rows = _rows_of(records)
    local = generic_columns.solve(rows)
    solution = local
    if document_solution is not None:
        if local is None or _explains(document_solution, rows) > _explains(local, rows):
            solution = document_solution
    items = []

    if solution is None:
        reason = generic_columns.diagnose(rows)
        section_name = records[0]["section"]
        warnings.append(f"section '{section_name}': {reason}")
        for record in records:
            items.append(_build(record, None, None, reason))
        return items

    for record, (qty, values) in zip(records, rows):
        price = total = None
        problem = None
        if qty in (None, 0):
            problem = "no quantity printed on this row, so its figures could not be checked"
        elif solution.price_index < len(values) and solution.total_index < len(values):
            price = values[solution.price_index]
            total = values[solution.total_index]
            ok, _ = generic_columns._row_matches(
                qty, values, solution.price_index, solution.total_index
            )
            if not ok:
                problem = (
                    "this row's figures don't multiply out the way the rest of the "
                    "section does"
                )
        else:
            problem = "this row has fewer columns than the rest of the section"
        items.append(_build(record, price, total, problem))
    return items


def _build(record, price, total, problem):
    description = re.sub(r"\s+", " ", " ".join(p for p in record["desc_parts"] if p).strip())
    item = LineItem(
        number=record["number"],
        description=description,
        quantity=record["quantity"],
        unit=record["unit"],
        section=record["section"],
        notes=list(record["notes"]),
        unit_price=price,
        rcv=total,
        raw_tail_tokens=list(record["tail_tokens"] or []),
        code_related=mentions_code_citation(description, *record["notes"]),
    )
    if problem:
        item.needs_review = True
        item.review_reason = problem
    return item


def parse_generic(lines, profile=GENERIC):
    """Returns (line_items, section_totals_raw, warnings) in exactly the
    shape line_items.parse_items_and_sections returns, so the rest of the
    pipeline treats a generically-read document identically."""
    warnings = []

    current_section = "Unknown"
    pending_title = None
    record = None
    segment = []
    # Collected first, resolved second: the document-wide arithmetic can
    # only be solved once every row has been seen.
    segments = []          # list of (records, totals_line or None)

    def close_record():
        nonlocal record
        if record is not None and record["tail_tokens"] is not None:
            segment.append(record)
        record = None

    def close_segment(totals_line=None):
        nonlocal segment
        segments.append((segment, totals_line))
        segment = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        m_total = profile.totals_re.match(stripped)
        if m_total and MONEY_RE.search(stripped):
            close_record()
            remainder = m_total.group(2)
            # "Exterior Plan - Subtotal (2 items) $0.00 ..." names its
            # section BEFORE the word Subtotal, not after it. Take that
            # when it's there, so the totals check reports a name a
            # contractor recognises instead of "(2 items)".
            prefix = stripped[: m_total.start(1)].strip(" -–\t")
            label = prefix or _split_leading_label(remainder) or current_section
            numbers = [parse_number(n) for n in MONEY_RE.findall(remainder)]
            close_segment((label, [n for n in numbers if n is not None]))
            continue

        if profile.continued_re is not None:
            m_cont = profile.continued_re.match(stripped)
            if m_cont:
                pending_title = m_cont.group(1).strip()
                continue

        m_item = profile.item_number_re.match(stripped) if profile.item_number_re else None
        if m_item:
            close_record()
            if pending_title:
                current_section, pending_title = pending_title, None
            number = m_item.group(1) + (m_item.group(2) or "")
            record = _new_record(number, current_section)
            if not _consume(m_item.group(3), record, profile):
                record["desc_parts"].append(m_item.group(3))
            continue

        if is_measurement_line(stripped):
            continue

        # Still waiting for this row's figures? Try to complete it.
        if record is not None and record["tail_tokens"] is None:
            if not _consume(stripped, record, profile):
                record["desc_parts"].append(stripped)
            continue

        # An unnumbered row: the quantity/unit anchor starts it. This is
        # how formats that don't number their line items get read at all.
        #
        # The anchor alone is not enough to call something a line item,
        # though. Measurement blocks and waste calculations ("346.13 SF",
        # "Auto Calculated Waste: 11.4%") carry a quantity and a unit too,
        # and on the Allstate fixture seventeen of them were picked up as
        # phantom line items until this guard was added. A real priced row
        # has a description AND figures beside it; these have neither.
        candidate = _new_record("", current_section)
        if (
            not has_labelled_measurement(stripped)
            and _consume(stripped, candidate, profile)
            and _is_priced_row(candidate)
        ):
            close_record()
            if pending_title:
                current_section, pending_title = pending_title, None
            candidate["section"] = current_section
            record = candidate
            continue

        if record is not None:
            if profile.page_furniture_re.search(stripped):
                continue
            words = stripped.split()
            if len(words) <= profile.max_continuation_words and not stripped.rstrip().endswith(
                (".", "!", "?", ":")
            ):
                record["desc_parts"].append(stripped)
            else:
                record["notes"].append(stripped)
            continue

        title = _title_candidate(stripped)
        if title:
            pending_title = title

    close_record()
    if segment:
        close_segment(None)

    # ---- second pass: solve, build, number -------------------------
    all_records = [r for records, _ in segments for r in records]
    document_solution = generic_columns.solve(_rows_of(all_records)) if all_records else None

    items = []
    section_totals = []
    for records, totals_line in segments:
        built = _finish_section(records, warnings, document_solution)
        items.extend(built)
        if totals_line is not None:
            running = round(sum(i.rcv for i in built if i.rcv is not None), 2)
            section_totals.append((totals_line[0], totals_line[1], running))

    # Formats that number their own rows keep their printed numbers.
    # Formats that don't get sequential ones, so the workspace has
    # something to refer to -- but only when nothing in the document was
    # numbered, so an invented number can never collide with a real one.
    if items and not any(i.number for i in items):
        for position, item in enumerate(items, start=1):
            item.number = str(position)

    return items, section_totals, warnings
