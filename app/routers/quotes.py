"""Quote CRUD, line-item persistence, and PDF export (tenant-scoped).

This is the API behind the quote builder page: create/list/detail quotes,
patch header fields, replace the line grid wholesale, and export a branded
PDF. The assembly math (apply-assembly) lives in app/routers/assemblies.py.
"""
import os
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services import quote_pdf

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

_EXPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "exports", "pdf",
)


def _get_owned_quote(db: Session, user: models.User, quote_id: int) -> models.Quote:
    quote = (
        db.query(models.Quote)
        .options(selectinload(models.Quote.items), selectinload(models.Quote.client))
        .filter(models.Quote.id == quote_id)
        .first()
    )
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    if quote.organization_id is not None and quote.organization_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your organization's quote",
        )
    return quote


def _quote_out(quote: models.Quote) -> dict:
    return {
        "id": quote.id,
        "organization_id": quote.organization_id,
        "client_id": quote.client_id,
        "client_name": quote.client.name if quote.client is not None else None,
        "title": quote.title,
        "site_address": quote.site_address,
        "status": quote.status,
        "subtotal": float(quote.subtotal or 0),
        "total": float(quote.total or 0),
        "created_at": quote.created_at,
        "line_count": len(quote.items or []),
    }


def _quote_detail_out(quote: models.Quote) -> dict:
    return {
        **_quote_out(quote),
        "lines": [
            schemas.QuoteLineOut.model_validate(item)
            for item in sorted(quote.items, key=lambda i: i.position or 0)
        ],
    }


def _recalculate_quote_totals(db: Session, quote: models.Quote) -> None:
    """Refresh a quote's subtotal/total from its stored line items."""
    subtotal = (
        db.query(func.coalesce(func.sum(models.QuoteLineItem.line_total), 0))
        .filter(models.QuoteLineItem.quote_id == quote.id)
        .scalar()
    )
    quote.subtotal = round(float(subtotal), 2)
    quote.total = quote.subtotal  # tax hooks in later
    db.add(quote)

@router.get("", response_model=list[schemas.QuoteOut])
def list_quotes(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """This organization's quotes, newest first."""
    query = (
        db.query(models.Quote)
        .options(selectinload(models.Quote.items), selectinload(models.Quote.client))
        .order_by(models.Quote.created_at.desc())
    )
    if current_user.organization_id is not None:
        query = query.filter(models.Quote.organization_id == current_user.organization_id)
    return [_quote_out(q) for q in query.all()]


@router.post("", response_model=schemas.QuoteOut, status_code=status.HTTP_201_CREATED)
def create_quote(
    payload: schemas.QuoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before creating quotes",
        )
    quote = models.Quote(
        organization_id=current_user.organization_id,
        title=payload.title or "Untitled quote",
        client_id=payload.client_id,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    quote.items = []
    return _quote_out(quote)


@router.get("/{quote_id}", response_model=schemas.QuoteDetailOut)
def get_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id))


@router.patch("/{quote_id}", response_model=schemas.QuoteOut)
def update_quote(
    quote_id: int,
    payload: schemas.QuoteUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    quote = _get_owned_quote(db, current_user, quote_id)
    for field in ("title", "site_address", "status", "client_id"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(quote, field, value)
    db.commit()
    db.refresh(quote)
    return _quote_out(quote)


@router.put("/{quote_id}/lines", response_model=schemas.QuoteDetailOut)
def save_lines(
    quote_id: int,
    payload: list[schemas.QuoteLineWrite],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Replace the quote's line grid wholesale and refresh totals."""
    quote = _get_owned_quote(db, current_user, quote_id)
    quote.items.clear()
    db.flush()
    for position, line in enumerate(payload, start=1):
        line_total = round(
            float(line.quantity) * float(line.unit_cost) * (1 + float(line.markup_percent) / 100.0),
            2,
        )
        db.add(models.QuoteLineItem(
            quote_id=quote.id,
            trade=line.trade,
            description=line.description,
            item_type=line.item_type,
            quantity=line.quantity,
            unit=line.unit,
            unit_cost=line.unit_cost,
            markup_percent=line.markup_percent,
            line_total=line_total,
            position=position,
        ))
    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    # Re-query so the response reflects the freshly written lines (rows
    # were added by FK, not via the relationship collection).
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id))


@router.delete("/{quote_id}")
def delete_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a quote: cascade its line items (ORM + ON DELETE CASCADE)
    and clean up any exported PDFs for it. 403 for another org's quote."""
    quote = _get_owned_quote(db, current_user, quote_id)
    # Remove exported PDFs for this quote (static/exports/pdf/quote-{id}-*.pdf).
    if os.path.isdir(_EXPORT_DIR):
        for name in os.listdir(_EXPORT_DIR):
            if name.startswith(f"quote-{quote_id}-") and name.endswith(".pdf"):
                try:
                    os.unlink(os.path.join(_EXPORT_DIR, name))
                except OSError:
                    pass
    db.delete(quote)
    db.commit()
    return {"deleted": True}


@router.delete("/{quote_id}/lines/{line_id}")
def delete_quote_line(
    quote_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Remove one line item, then recalculate the quote totals."""
    quote = _get_owned_quote(db, current_user, quote_id)
    line = (
        db.query(models.QuoteLineItem)
        .filter(
            models.QuoteLineItem.id == line_id,
            models.QuoteLineItem.quote_id == quote.id,
        )
        .first()
    )
    if line is None:
        raise HTTPException(status_code=404, detail="Line item not found")
    # Drop it from the in-memory collection too, so the later
    # db.add(quote) cascade never touches a deleted instance.
    quote.items = [item for item in (quote.items or []) if item.id != line.id]
    db.delete(line)
    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id))


@router.get("/{quote_id}/pdf")
def export_quote_pdf(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Render the quote to a branded PDF under /static/exports/pdf and
    return the URL (kept for the page's download-history list)."""
    quote = _get_owned_quote(db, current_user, quote_id)
    if not quote.items:
        raise HTTPException(status_code=400, detail="Add line items before exporting")

    os.makedirs(_EXPORT_DIR, exist_ok=True)
    filename = f"quote-{quote.id}-{int(time.time())}.pdf"
    out_path = os.path.join(_EXPORT_DIR, filename)
    context = {
        "quote": quote,
        "client": quote.client,
        "organization": current_user.organization,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "today": time.strftime("%B %d, %Y"),
    }
    quote_pdf.render_quote_pdf(context, out_path)
    return {"url": f"/static/exports/pdf/{filename}", "filename": filename}

