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
from app.services import assembly_service
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
    persisted flat tax rate, and its contingency buffer (mirrors quotes.py)."""
    subtotal = (
        db.query(func.coalesce(func.sum(models.QuoteLineItem.line_total), 0))
        .filter(models.QuoteLineItem.quote_id == quote.id)
        .scalar()
    )
    quote.subtotal = round(float(subtotal), 2)
    rate = float(quote.tax_rate_percent or 0)
    quote.tax_amount = round(quote.subtotal * rate / 100.0, 2)
    contingency = round(quote.subtotal * float(quote.contingency_percent or 0) / 100.0, 2)
    quote.total = round(quote.subtotal + quote.tax_amount + contingency, 2)
    db.add(quote)


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

    # Remember the room dimensions for the crew work order.
    quote.scope_dimensions = {k: float(v) for k, v in (payload.dimensions or {}).items()}
    db.add(quote)

    next_position = (
        db.query(func.coalesce(func.max(models.QuoteLineItem.position), 0))
        .filter(models.QuoteLineItem.quote_id == quote.id)
        .scalar()
    ) + 1

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
            position=next_position,
        ))
        next_position += 1

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
        "summary": summary,
    }
