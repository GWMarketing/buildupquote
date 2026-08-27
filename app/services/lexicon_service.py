"""Trade lexicon matching -- turn free-text descriptions into trades.

The lexicon holds standard terms plus the slang a contractor actually
uses ("sheetrock", "gyprock", "2x4"...). Matching is a simple substring
check, but candidates are sorted by LENGTH so the most specific term
wins over a short alias that happens to appear inside another word.
"""
from sqlalchemy.orm import Session

from app import models

_DEFAULT_TRADE = "General"


def match_trade_from_description(description: str, db: Session) -> str:
    """Return the best-guess trade ("Drywall", "Carpentry"...) for a
    description, or "General" when nothing matches."""
    text = " ".join(str(description or "").lower().split())
    if not text:
        return _DEFAULT_TRADE

    candidates = []  # (trade, term_or_alias) pairs
    for row in db.query(models.TradeLexicon).all():
        candidates.append((row.trade, str(row.term or "").lower()))
        for alias in row.aliases or []:
            candidates.append((row.trade, str(alias or "").lower()))
    # Most specific (longest) candidate first, so "treated timber board"
    # beats the generic "board" that would otherwise match everything.
    # On equal length, prefer a real trade over the catch-all "general".
    candidates.sort(key=lambda pair: (len(pair[1]), pair[0] != "general"), reverse=True)
    for trade, term in candidates:
        if term and term in text:
            return trade.title()
    return _DEFAULT_TRADE
