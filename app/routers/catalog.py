"""Trade catalog autocorrect + assembly calculators (PostgreSQL trigram).

GET /api/catalog/autocorrect?q=... is the typeahead behind the quote
builder's description combobox. It resolves a half-typed term ('sheetro',
'2x4', 'thins') to a canonical priced catalog item by matching the raw
aliases AND the canonical name, ranked by the best PostgreSQL trigram
similarity score. On non-PostgreSQL databases (SQLite in dev and tests) it
falls back to a contains match so the endpoint keeps working.

POST /api/catalog/calculate-assembly runs a hand-written Python assembly
calculator (stud_wall / floor_tiling) for the given dimensions and returns
the normalized, priced line items.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services.assembly_service import AssemblyFormulaError, calculate_calculator

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# assembly_type -> calculator name registered in assembly_calculators.py
_ASSEMBLY_TYPE_TO_CALCULATOR = {
    "stud_wall": "calculate_partition_wall",
    "floor_tiling": "calculate_floor_tiling",
}


def _autocorrect_postgres(db: Session, query: str, limit: int):
    """Trigram similarity over canonical names AND raw aliases, best score
    wins -- the raw-SQL form (PostgreSQL only)."""
    sql = text(
        """
        SELECT
            c.id, c.trade, c.canonical_name, c.unit, c.default_unit_cost,
            c.default_trade_type,
            GREATEST(similarity(c.canonical_name, :q),
                     COALESCE(MAX(similarity(s.raw_term, :q)), 0)) AS match_score
        FROM trade_catalog_items c
        LEFT JOIN trade_synonyms s ON c.id = s.catalog_id
        WHERE c.canonical_name ILIKE :wildcard
           OR s.raw_term ILIKE :wildcard
           OR similarity(c.canonical_name, :q) > 0.25
           OR similarity(s.raw_term, :q) > 0.25
        GROUP BY c.id
        ORDER BY match_score DESC
        LIMIT :limit
        """
    )
    return db.execute(sql, {"q": query, "wildcard": f"%{query}%", "limit": limit}).fetchall()


def _autocorrect_fallback(db: Session, query: str, limit: int):
    """Contains match over canonical names AND raw aliases (SQLite in dev
    and tests) -- same result shape, no Postgres-specific SQL."""
    return (
        db.query(models.TradeCatalogItem, models.TradeSynonym)
        .outerjoin(models.TradeSynonym, models.TradeCatalogItem.id == models.TradeSynonym.catalog_id)
        .filter(or_(
            models.TradeCatalogItem.canonical_name.ilike(f"%{query}%"),
            models.TradeSynonym.raw_term.ilike(f"%{query}%"),
        ))
        .limit(limit * 4)
        .all()
    )


def _row_to_result(row) -> dict:
    return {
        "id": row.id,
        "trade": row.trade,
        "canonical_name": row.canonical_name,
        "unit": row.unit,
        "default_unit_cost": float(row.default_unit_cost or 0),
        # The DB stores display labels ("Material"); the quote builder's
        # item_type contract is lowercase, so normalize on the way out.
        "default_trade_type": (row.default_trade_type or "material").lower(),
    }


@router.get("/autocorrect")
def autocorrect_trade_search(
    q: str = Query("", max_length=80),
    limit: int = Query(6, ge=1, le=25),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Typeahead suggestions: {id, trade, canonical_name, unit,
    default_unit_cost, default_trade_type}. Postgres ranks by the best
    trigram similarity of the canonical name or any raw alias; other
    databases use a contains match. Short queries return no results."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": []}

    results = []
    seen = set()
    if db.get_bind().dialect.name == "postgresql":
        rows = _autocorrect_postgres(db, query, limit)
        for row in rows:
            if row.canonical_name in seen:
                continue
            seen.add(row.canonical_name)
            results.append(_row_to_result(row))
    else:
        for item, _synonym in _autocorrect_fallback(db, query, limit):
            if item.canonical_name in seen:
                continue
            seen.add(item.canonical_name)
            results.append(_row_to_result(item))
    return {"results": results}


@router.post("/calculate-assembly")
def calculate_assembly(
    payload: schemas.AssemblyBuildRequest,
    current_user: models.User = Depends(get_current_user),
):
    """Run a hand-written assembly calculator ('stud_wall' or
    'floor_tiling') and return its normalized, priced lines."""
    calc_name = _ASSEMBLY_TYPE_TO_CALCULATOR.get(payload.assembly_type)
    if calc_name is None:
        raise HTTPException(status_code=400, detail="Unknown assembly type")

    dimensions = {"length": payload.length}
    if payload.width:
        dimensions["width"] = payload.width
    if payload.height:
        dimensions["height"] = payload.height
    try:
        lines = calculate_calculator(calc_name, dimensions)
    except AssemblyFormulaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "assembly_type": payload.assembly_type,
        "lines": lines,
        "total": round(sum(float(line["subtotal"]) for line in lines), 2),
    }


# ---------------------------------------------------------------------------
# Rate catalog management (the Catalog page: browse / add / delete items)
# ---------------------------------------------------------------------------

@router.get("/items", response_model=list[schemas.CatalogItemOut])
def list_catalog_items(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Every standard rate item, ordered by trade then name."""
    return (
        db.query(models.TradeCatalogItem)
        .order_by(models.TradeCatalogItem.trade, models.TradeCatalogItem.canonical_name)
        .all()
    )


@router.post("/items", response_model=schemas.CatalogItemOut, status_code=status.HTTP_201_CREATED)
def create_catalog_item(
    payload: schemas.CatalogItemCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add a standard rate item. canonical_name is unique (case-insensitive)."""
    trade = (payload.trade or "").strip()
    name = (payload.canonical_name or "").strip()
    if not trade or not name:
        raise HTTPException(status_code=400, detail="Trade and name are required")
    exists = (
        db.query(models.TradeCatalogItem)
        .filter(func.lower(models.TradeCatalogItem.canonical_name) == name.lower())
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="That catalog item already exists")
    item = models.TradeCatalogItem(
        trade=trade,
        canonical_name=name,
        unit=(payload.unit or "").strip() or "unit",
        default_unit_cost=payload.default_unit_cost or 0,
        default_trade_type=(payload.default_trade_type or "Material").strip() or "Material",
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="That catalog item already exists")
    db.refresh(item)
    return item


@router.delete("/items/{item_id}")
def delete_catalog_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Remove a rate item from the catalog."""
    item = (
        db.query(models.TradeCatalogItem)
        .filter(models.TradeCatalogItem.id == item_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    db.delete(item)
    db.commit()
    return {"deleted": True}

