"""Recognizing a building-code citation, shared by line_items.py (which
already had to spot one to tell a code-justification note apart from a
genuine description continuation -- see _NOTE_TRIGGERS) and claim_flags.py
(which uses the same signal to answer "how much of this estimate is
code-driven," feeding the Ordinance-or-Law coverage check in the Claim
Ledger reference doc).

Deliberately narrow: this only matches an actual section-style citation
(IRC, IBC, or an R9xx/1503.x/1504.x/1609.x section number), not general
roofing vocabulary -- so it's safe to use as a real signal rather than
something that fires on every other line item.
"""
import re

CODE_CITATION_RE = re.compile(
    r"\bIRC\b|\bIBC\b|\bR9\d{2}(\.\d+)*\b|\b1503\.\d+\b|\b1504\.\d+\b|\b1609\.\d+\b",
    re.IGNORECASE,
)


def mentions_code_citation(*texts) -> bool:
    """True if any of `texts` (descriptions, notes, whatever) contains a
    recognizable building-code citation. None/empty strings are ignored."""
    return any(t and CODE_CITATION_RE.search(t) for t in texts)
