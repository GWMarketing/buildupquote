"""Parametric assemblies + trade lexicon endpoints (Phase 5).

Assemblies are tenant-scoped: every endpoint shows global templates plus
the current user's own organization's assemblies, and applying one to a
quote is guarded by organization ownership.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services import assembly_service, quote_service
from app.services.lexicon_service import match_trade_from_description

router = APIRouter(prefix="/api", tags=["assemblies"])


def _assembly_query(db: Session, user: models.User):
    """Global templates (organization_id IS NULL) + this org's own."""
    q = db.query(models.ParametricAssembly)
    if user.organization_id is not None:
        q = q.filter(or_(
            models.ParametricAssembly.organization_id.is_(None),
            models.ParametricAssembly.organization_id == user.organization_id,
        ))
    else:
        q = q.filter(models.ParametricAssembly.organization_id.is_(None))
    return q


def _recalculate_quote_totals(db: Session, quote: models.Quote) -> None:
    """Refresh a quote's subtotal/tax/total from its stored line items, its
    persisted flat tax rate, and its contingency buffer (mirrors quotes.py;
    optional add-ons the client hasn't selected are excluded)."""
    quote_service.recalculate_quote_totals(db, quote)


def _append_generated_lines(db: Session, quote: models.Quote, lines: list[dict]) -> list[models.QuoteLineItem]:
    """Insert generated assembly lines (with trade tagging), flush, and return
    the freshly persisted rows so the caller can record their ids."""
    start = (
        db.query(func.coalesce(func.max(models.QuoteLineItem.position), 0))
        .filter(models.QuoteLineItem.quote_id == quote.id)
        .scalar()
    ) + 1
    pos = start
    for line in lines:
        db.add(models.QuoteLineItem(
            quote_id=quote.id,
            # Calculator lines carry their trade; formula components fall
            # back to the trade lexicon.
            trade=line.get("trade") or match_trade_from_description(line["description"], db),
            description=line["description"],
            item_type=line["item_type"],
            quantity=line["quantity"],
            unit=line["unit"],
            unit_cost=line["unit_cost"],
            markup_percent=line["markup_percent"],
            line_total=line["subtotal"],
            position=pos,
        ))
        pos += 1
    db.flush()
    return (
        db.query(models.QuoteLineItem)
        .filter(
            models.QuoteLineItem.quote_id == quote.id,
            models.QuoteLineItem.position >= start,
        )
        .order_by(models.QuoteLineItem.position)
        .all()
    )


@router.get("/assemblies", response_model=list[schemas.AssemblyOut])
def list_assemblies(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Global templates plus this organization's own assemblies."""
    return (
        _assembly_query(db, current_user)
        .options(selectinload(models.ParametricAssembly.components))
        .order_by(models.ParametricAssembly.code)
        .all()
    )


@router.post("/assemblies/{code}/calculate", response_model=schemas.AssemblyCalculateResponse)
def calculate_assembly(
    code: str,
    payload: schemas.AssemblyCalculateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Run the math engine: evaluate every component formula against the
    supplied dimensions and return the priced line items."""
    assembly = (
        _assembly_query(db, current_user)
        .filter(models.ParametricAssembly.code == code)
        .first()
    )
    if assembly is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assembly not found")
    try:
        lines, summary = assembly_service.calculate_assembly_with_summary(
            assembly, payload.dimensions, payload.waste_percent,
        )
    except assembly_service.AssemblyFormulaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "assembly_code": assembly.code,
        "assembly_name": assembly.name,
        "lines": lines,
        "total": round(sum(float(line["subtotal"]) for line in lines), 2),
        "summary": summary,
    }


@router.post("/quotes/{quote_id}/apply-assembly")
def apply_assembly(
    quote_id: int,
    payload: schemas.ApplyAssemblyRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Calculate an assembly and append every generated line to the target
    quote, refreshing the quote totals automatically. 403 if the quote
    belongs to a different organization."""
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    if quote.organization_id is not None and quote.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your organization's quote",
        )
    if quote.status == "accepted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This quote has been accepted and signed — it can no longer be edited",
        )

    assembly = (
        _assembly_query(db, current_user)
        .filter(models.ParametricAssembly.code == payload.code)
        .first()
    )
    if assembly is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assembly not found")

    try:
        lines, summary = assembly_service.calculate_assembly_with_summary(
            assembly, payload.dimensions, payload.waste_percent,
        )
    except assembly_service.AssemblyFormulaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Labor-only install ("client supplies materials"): keep the labor lines
    # (hours, hourly rate, gross margin) but skip every material line so the
    # quote bills zero material quantity/cost for this assembly.
    if payload.labor_only:
        lines = [line for line in lines if line["item_type"] == "labor"]

    # Remember the room dimensions for the crew work order.
    quote.scope_dimensions = {k: float(v) for k, v in (payload.dimensions or {}).items()}
    db.add(quote)

    new_items = _append_generated_lines(db, quote, lines)

    # Record the applied room for the batch replicator.
    room_name = (payload.room_name or "").strip() or assembly.name or assembly.code
    rooms = list(quote.rooms or [])
    rooms.append({
        "key": max([r.get("key", 0) for r in rooms], default=-1) + 1,
        "name": room_name,
        "assembly_code": assembly.code,
        "dimensions": {k: float(v) for k, v in (payload.dimensions or {}).items()},
        "waste_percent": float(payload.waste_percent or 0),
        "labor_only": bool(payload.labor_only),
        "line_ids": [item.id for item in new_items],
    })
    quote.rooms = rooms
    db.add(quote)

    # autoflush is OFF on this session, so flush the new lines before the
    # totals query can see them.
    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    db.refresh(quote)
    return {
        "quote_id": quote.id,
        "quote_title": quote.title,
        "assembly_code": assembly.code,
        "added_lines": lines,
        "quote_subtotal": float(quote.subtotal),
        "quote_total": float(quote.total),
        "line_count": len(lines),
        "rooms": quote.rooms,
        "summary": summary,
    }


@router.post("/quotes/{quote_id}/duplicate-room")
def duplicate_room(
    quote_id: int,
    payload: schemas.DuplicateRoomRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Batch room replicator: re-run a recorded assembly against NEW
    dimensions and append the result (materials specs, waste factor, labor
    rates all recomputed) as a fresh room on the active quote."""
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    if quote.organization_id is not None and quote.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your organization's quote",
        )
    if quote.status == "accepted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This quote has been accepted and signed — it can no longer be edited",
        )

    rooms = list(quote.rooms or [])
    source = next((r for r in rooms if r.get("key") == payload.room_key), None)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    assembly = (
        _assembly_query(db, current_user)
        .filter(models.ParametricAssembly.code == source.get("assembly_code"))
        .first()
    )
    if assembly is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assembly not found")

    try:
        lines, _summary = assembly_service.calculate_assembly_with_summary(
            assembly, payload.dimensions, source.get("waste_percent") or 0,
        )
    except assembly_service.AssemblyFormulaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if source.get("labor_only"):
        lines = [line for line in lines if line["item_type"] == "labor"]

    quote.scope_dimensions = {k: float(v) for k, v in (payload.dimensions or {}).items()}
    new_items = _append_generated_lines(db, quote, lines)

    rooms.append({
        "key": max([r.get("key", 0) for r in rooms], default=-1) + 1,
        "name": (payload.name or "").strip() or f"{source.get('name')} copy",
        "assembly_code": assembly.code,
        "dimensions": {k: float(v) for k, v in (payload.dimensions or {}).items()},
        "waste_percent": float(source.get("waste_percent") or 0),
        "labor_only": bool(source.get("labor_only")),
        "line_ids": [item.id for item in new_items],
    })
    quote.rooms = rooms
    db.add(quote)

    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    db.refresh(quote)
    return {
        "quote_id": quote.id,
        "added_lines": lines,
        "quote_subtotal": float(quote.subtotal),
        "quote_total": float(quote.total),
        "line_count": len(lines),
        "rooms": quote.rooms,
    }
