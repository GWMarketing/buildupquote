"""Multi-trade lexicon search.

PostgreSQL path calls the search_trade_lexicon() stored function created at
startup (trigram similarity + ts_rank over the search_vector corpus). Any
other database (SQLite in dev/tests) uses a contains fallback over the same
columns -- matching the trade catalog's autocorrect pattern.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import models
from app.seeds.lexicon._shared import serialize_row


def _postgres(db: Session, query: str, trade: str | None, limit: int) -> list:
    return db.execute(
        text("SELECT * FROM search_trade_lexicon(:q, :limit, :trade_filter)"),
        {"q": query, "trade_filter": trade, "limit": limit},
    ).mappings().all()


def _fallback(db: Session, query: str, trade: str | None, limit: int) -> list:
    """Contains match over the search corpus (canonical + aliases + typos),
    (SQLite / dev / tests) -- same result shape as Postgres. The corpus is
    the precomputed search_vector text column, so no JSON casting is needed."""
    q = f"%{query}%"
    base = db.query(models.TradeLexicon).filter(
        models.TradeLexicon.search_vector.ilike(q)
    )
    if trade:
        base = base.filter(models.TradeLexicon.trade == trade)
    return base.limit(limit * 4).all()


def search_trade_lexicon(
    db: Session,
    query: str,
    trade: str | None = None,
    limit: int = 25,
) -> list:
    """Ranked lexicon matches for `query`, optionally filtered to one trade.
    Returns spec-shaped dicts (trade_category, canonical_term, spoken_aliases,
    phonetic_respelling, ipa_pronunciation, ...)."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    if db.get_bind().dialect.name == "postgresql":
        rows = _postgres(db, query, trade, limit)
        results = []
        seen = set()
        for row in rows:
            if row["canonical_term"] in seen:
                continue
            seen.add(row["canonical_term"])
            results.append({
                "id": row["id"],
                "uuid": row["uuid"],
                "trade_category": row["trade_category"],
                "canonical_term": row["canonical_term"],
                "spoken_aliases": row["spoken_aliases"] or [],
                "phonetic_respelling": row["phonetic_respelling"] or "",
                "ipa_pronunciation": row["ipa_pronunciation"] or "",
                "common_misspellings_typos": row["common_misspellings_typos"] or [],
                "unit_of_measure": row["unit_of_measure"] or "EA",
                "definition_and_use": row["definition_and_use"] or "",
                "match_score": row["match_score"],
            })
            if len(results) >= limit:
                break
        return results

    # SQLite / dev / tests fallback: contains over the search corpus.
    rows = _fallback(db, query, trade, limit)
    results = []
    seen = set()
    for row in rows:
        if row.term in seen:
            continue
        seen.add(row.term)
        results.append(serialize_row(row))
        if len(results) >= limit:
            break
    return results

