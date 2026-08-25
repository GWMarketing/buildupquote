"""Strips CAD sketch / floor-plan noise out of the extracted text.

Xactimate-style PDFs embed the room/roof sketch as a page of text tokens
(facet labels like "R4 (2)" or "F2(B)", and raw dimension strings like
"12' 10\"") sitting right next to the measurement summary we actually want
("3,397.66 Surface Area"). See the project's "Known parsing problems" doc,
problem #1.

This module works line-by-line on already-extracted text (see extract.py
for the PDF -> text step). A line is classified as noise if essentially
every token on it is a sketch label or a bare dimension, and it has no
sentence-like content at all.
"""
import re

_FEET = re.compile(r"^\d+'$")
_INCHES = re.compile(r'^\d+"$')
_FEET_INCHES = re.compile(r'''^\d+'\d{1,2}"$''')
_FACET_LABEL = re.compile(r"^([RF]\d+(\([A-Za-z0-9]+\))?)+$")  # R4, F2(B), R10, or a
# run of these squashed together with no space by the PDF extractor, e.g.
# "R10F8(A)" -- seen in real Allstate/Xactimate sketch output.
_PAREN_NUM = re.compile(r"^\(\d+\)$")  # (2)
_BARE_ROOM_TAG = re.compile(r"^[A-Za-z]{1,2}\d{1,3}$")  # R10, F11 already covered but keep cheap catch-all

_NOISE_TOKEN_PATTERNS = (
    _FEET, _INCHES, _FEET_INCHES, _FACET_LABEL, _PAREN_NUM, _BARE_ROOM_TAG,
)


def _is_noise_token(token: str) -> bool:
    return any(p.match(token) for p in _NOISE_TOKEN_PATTERNS)


def is_noise_line(line: str) -> bool:
    """True if a line is sketch/CAD clutter rather than real content."""
    stripped = line.strip()
    if not stripped:
        return False
    tokens = stripped.split()
    if not tokens:
        return False
    noisy = sum(1 for t in tokens if _is_noise_token(t))
    # Require *every* token to look like sketch clutter -- a line that mixes
    # in even one real word ("Bathroom", "Height:", a unit price, etc.)
    # should be kept and handled by the real parsing logic instead.
    return noisy == len(tokens)


def strip_noise(lines):
    """Split lines into (kept, discarded) preserving order in `kept`."""
    kept, discarded = [], []
    for line in lines:
        if is_noise_line(line):
            discarded.append(line)
        else:
            kept.append(line)
    return kept, discarded
