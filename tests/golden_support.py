"""Shared helper for the golden-snapshot regression lock.

WHY THIS EXISTS
---------------
The Xactimate parsing behaviour in this package is the product's crown
jewels: it was hardened against three real carrier PDFs, one bug at a
time, and every one of those fixes is invisible in the code but very
visible in the output. Any refactor that generalises the parser to other
estimating programs must not change a single one of those outputs.

Ordinary unit tests check individual behaviours a human thought to assert.
A golden snapshot checks *everything the parser currently produces* --
including the parts nobody wrote an assertion for. If a refactor shifts a
description by one word, drops a note, renames a section, or rounds a
number differently, the snapshot diff says so immediately.

The rule: `python3 tools/refresh_golden.py` is only ever run on purpose,
and the resulting diff is read line by line before it is committed. A
snapshot that changes without an intended reason is a bug report.
"""
import dataclasses
import json
import os

FIXTURE_NAMES = (
    "allstate_5410",
    "travelers_erin",
    "appraiser_williams1",
    # Added 2026-08-25, both from real claims that failed to parse:
    "contractor_doyle",           # Xactimate contractor export -- RESET/REMOVE/REPLACE columns
    "symbility_libertymutual",    # Symbility/Cotality -- quantity, price, THEN unit
)

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(_HERE, "fixtures")
GOLDEN_DIR = os.path.join(_HERE, "golden")


def fixture_text(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name + ".txt"), encoding="utf-8") as fh:
        return fh.read()


def golden_path(name: str) -> str:
    return os.path.join(GOLDEN_DIR, name + ".json")


def _plain(value):
    """dataclass tree -> plain JSON-able data, with floats rounded to cents.

    Rounding matters: a refactor is allowed to reach $2,039.08 by a
    different arithmetic route, but it is not allowed to reach a different
    number. Without rounding, harmless float representation noise would
    make this lock cry wolf and get ignored -- which is worse than not
    having it.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 2)
    return value


# The fields the lock covers: everything the parser EXTRACTS. Fields that
# merely describe the parse itself (which rule sheet was chosen, what kind
# of document it is, how confident we are) are deliberately excluded --
# they were added after the lock was set, they change as new formats are
# supported, and locking them would make the lock fire on work that
# changed nothing about the extracted estimate. Their own behaviour is
# covered by ordinary tests in test_formats.py instead.
LOCKED_KEYS = (
    "metadata",
    "line_items",
    "measurements",
    "section_totals",
    "discarded_lines",
    "document_notes",
    "warnings",
    "claim_flags",
)


def snapshot(estimate) -> dict:
    """The full, ordered, comparable shape of one parsed estimate's
    EXTRACTED content -- see LOCKED_KEYS."""
    data = _plain(estimate)
    return {k: v for k, v in data.items() if k in LOCKED_KEYS}


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_fixture(name: str):
    from scope_parser.pipeline import parse_text

    return parse_text(fixture_text(name))
