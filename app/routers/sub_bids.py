"""Subcontractor bid-request links.

The contractor picks scope lines on a quote and gets a secure public
/sub-bid/<token> URL. The subcontractor sees ONLY the sanitized scope --
project address, room dimensions, line descriptions, contractor notes (never
pricing, margins, or client contact info) -- and submits a lump-sum bid. The
bid is written back onto the master quote as a subcontractor line.
"""
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services import quote_service

router = APIRouter(tags=["sub-bids"])

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _owned_quote(db: Session, user: models.User, quote_id: int) -> models.Quote:
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if quote is None or quote.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


def _selected_rows(db: Session, quote: models.Quote, line_ids) -> list[models.QuoteLineItem]:
    """The quote's lines for the given ids (tolerates bad/missing ids)."""
    ids = {int(i) for i in (line_ids or [])}
    return [line for line in quote.items if line.id in ids]


def _sub_out(sub: models.SubBid) -> dict:
    out = schemas.SubBidOut.model_validate(sub).model_dump()
    out["url"] = f"/sub-bid/{sub.token}"
    return out


# ---------------------------------------------------------------------------
# Contractor API
# ---------------------------------------------------------------------------
@router.post("/api/quotes/{quote_id}/sub-bids", response_model=schemas.SubBidOut)
def create_sub_bid(
    quote_id: int,
    payload: schemas.SubBidCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a secure public sub-bid link for a set of scope lines."""
    quote = _owned_quote(db, current_user, quote_id)
    if quote.status == "accepted":
        raise HTTPException(
            status_code=400,
            detail="This quote is already accepted and signed — request the sub-bid first",
        )
    rows = _selected_rows(db, quote, payload.line_ids)
    if not rows:
        raise HTTPException(status_code=400, detail="Pick at least one scope line")

    sub = models.SubBid(
        quote_id=quote.id,
        token=str(uuid.uuid4()),
        status="open",
        selected_line_ids=[line.id for line in rows],
        contractor_notes=(payload.notes or "").strip() or None,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return _sub_out(sub)


@router.get("/api/quotes/{quote_id}/sub-bids", response_model=list[schemas.SubBidOut])
def list_sub_bids(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The contractor's sub-bid links + submission status for this quote."""
    quote = _owned_quote(db, current_user, quote_id)
    subs = (
        db.query(models.SubBid)
        .filter(models.SubBid.quote_id == quote.id)
        .order_by(models.SubBid.created_at.desc())
        .all()
    )
    return [_sub_out(s) for s in subs]


# ---------------------------------------------------------------------------
# Public subcontractor view -- sanitized scope, zero pricing/client data
# ---------------------------------------------------------------------------
@router.get("/sub-bid/{token}")
def sub_bid_view(token: str, request: Request, db: Session = Depends(get_db)):
    sub = db.query(models.SubBid).filter(models.SubBid.token == token).first()
    if sub is None:
        return templates.TemplateResponse(request, "sub_bid.html", {"not_found": True})
    quote = db.query(models.Quote).filter(models.Quote.id == sub.quote_id).first()
    if quote is None:
        return templates.TemplateResponse(request, "sub_bid.html", {"not_found": True})

    rows = _selected_rows(db, quote, sub.selected_line_ids)
    return templates.TemplateResponse(request, "sub_bid.html", {
        "not_found": False,
        "sub_bid": sub,
        "quote_title": quote.title,
        "site_address": quote.site_address,
        "dimensions": quote.scope_dimensions or {},
        "scope": rows,
        "contractor_notes": sub.contractor_notes,
        "submitted": sub.status == "submitted",
        "bid_amount": float(sub.bid_amount) if sub.bid_amount is not None else None,
        "bidder_name": sub.bidder_name,
        "bid_notes": sub.bid_notes,
    })


@router.post("/sub-bid/{token}/submit")
def submit_sub_bid(
    token: str,
    payload: schemas.SubBidSubmitRequest,
    db: Session = Depends(get_db),
):
    """The subcontractor submits their lump-sum bid. On success the bid is
    written onto the contractor's master quote as a subcontractor line."""
    sub = db.query(models.SubBid).filter(models.SubBid.token == token).first()
    if sub is None:
        raise HTTPException(status_code=404, detail="Bid link not found")
    if sub.status == "submitted":
        raise HTTPException(status_code=400, detail="This bid has already been submitted")
    bid = round(float(payload.bid_amount or 0), 2)
    if bid <= 0:
        raise HTTPException(status_code=400, detail="Enter a valid bid amount")

    quote = db.query(models.Quote).filter(models.Quote.id == sub.quote_id).first()
    if quote is None or quote.status == "accepted":
        raise HTTPException(
            status_code=400,
            detail="The master quote is no longer open to edits",
        )

    sub.bid_amount = bid
    sub.bid_notes = (payload.notes or "").strip() or None
    sub.bidder_name = (payload.bidder_name or "").strip() or None
    sub.status = "submitted"
    sub.submitted_at = datetime.now(timezone.utc)
    db.add(sub)

    # Populate the master quote's labor/sub cost with this subcontractor's bid.
    rows = _selected_rows(db, quote, sub.selected_line_ids)
    trade = next((r.trade for r in rows if r.trade), None) or "trade"
    next_position = max([r.position or 0 for r in quote.items], default=0) + 1
    db.add(models.QuoteLineItem(
        quote_id=quote.id,
        trade=trade,
        description=f"Subcontractor bid — {trade} (via sub-bid link)",
        item_type="subcontractor",
        quantity=1,
        unit="lump",
        unit_cost=bid,
        markup_percent=0,
        line_total=bid,
        position=next_position,
    ))
    db.flush()
    quote_service.recalculate_quote_totals(db, quote)
    db.commit()
    return {
        "ok": True,
        "status": "submitted",
        "bid_amount": bid,
        "quote_total": float(quote.total or 0),
    }

