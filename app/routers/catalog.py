"""Trade catalog autocorrect (PostgreSQL trigram).

GET /api/catalog/autocorrect?q=... is the typeahead behind the quote
builder's description combobox: it resolves whatever a contractor half-types
('sheetro', '2x4', 'thins') to a canonical priced catalog item using
pg_trgm similarity on raw terms. On non-PostgreSQL databases (SQLite in dev
and tests) it falls back to a contains match so the endpoint still works.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/autocorrect")
def autocorrect(
    q: str = Query("", max_length=80),
    limit: int = Query(8, ge=1, le=25),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Typeahead suggestions: {canonical_name, trade, unit, default_unit_cost,
    default_trade_type}. Postgres ranks raw terms by trigram similarity and
    keeps matches above 0.3; other databases use a contains match."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": []}

    base = db.query(models.TradeSynonym).join(models.TradeCatalogItem)
    if db.get_bind().dialect.name == "postgresql":
        similarity = func.similarity(models.TradeSynonym.raw_term, query)
        rows = (
            base.filter(similarity > 0.3)
            .order_by(similarity.desc())
            .limit(limit * 4)
            .all()
        )
    else:
        rows = (
            base.filter(models.TradeSynonym.raw_term.ilike(f"%{query}%"))
            .limit(limit * 4)
            .all()
        )

    results = []
    seen = set()
    for synonym in rows:
        item = synonym.catalog_item
        if item.canonical_name in seen:
            continue
        seen.add(item.canonical_name)
        results.append({
            "canonical_name": item.canonical_name,
            "trade": item.trade,
            "unit": item.unit,
            "default_unit_cost": float(item.default_unit_cost or 0),
            # The DB stores display labels ("Material"); the quote builder's
            # item_type contract is lowercase, so normalize on the way out.
            "default_trade_type": (item.default_trade_type or "material").lower(),
        })
        if len(results) >= limit:
            break
    return {"results": results}
