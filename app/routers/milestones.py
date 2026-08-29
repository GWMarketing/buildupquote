"""Milestone draw requests & phase sign-off.

For an in-progress job (sent/accepted), the contractor requests a draw on an
unreleased payment stage, attaches up to 3 completion photos + notes, and the
homeowner approves it on a secure /milestone/<token> page -- which releases
the stage and records the approval.
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
from app.services import quote_pdf

router = APIRouter(tags=["milestones"])

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
templates.env.filters["money"] = quote_pdf.format_money

_MAX_PHOTOS = 3


def _owned_quote(db: Session, user: models.User, quote_id: int) -> models.Quote:
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if quote is None or quote.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


def _schedule(quote: models.Quote) -> list[dict]:
    return [
        {
            "label": m.get("label") or "Stage",
            "percent": round(float(m.get("percent") or 0), 2),
            "released": bool(m.get("released")),
        }
        for m in (quote.payment_schedule or [])
    ]


def _draw_out(draw: models.MilestoneDraw) -> dict:
    out = schemas.MilestoneDrawOut.model_validate(draw).model_dump()
    out["url"] = f"/milestone/{draw.token}"
    return out


@router.post("/api/quotes/{quote_id}/milestone-draws", response_model=schemas.MilestoneDrawOut)
def create_milestone_draw(
    quote_id: int,
    payload: schemas.MilestoneDrawCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Request a draw on an unreleased payment stage (active/in-progress job)."""
    quote = _owned_quote(db, current_user, quote_id)
    if quote.status not in ("sent", "accepted"):
        raise HTTPException(status_code=400, detail="Milestone draws are for active/in-progress jobs")

    schedule = _schedule(quote)
    if not schedule:
        raise HTTPException(status_code=400, detail="Set a payment schedule first")
    if payload.milestone_index < 0 or payload.milestone_index >= len(schedule):
        raise HTTPException(status_code=400, detail="Invalid payment stage")
    stage = schedule[payload.milestone_index]
    if stage["released"]:
        raise HTTPException(status_code=400, detail="This payment stage is already released")
    photos = [p for p in (payload.photos or []) if isinstance(p, str) and p.strip()]
    if len(photos) > _MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"Attach at most {_MAX_PHOTOS} photos")

    draw = models.MilestoneDraw(
        quote_id=quote.id,
        token=str(uuid.uuid4()),
        milestone_index=payload.milestone_index,
        milestone_label=stage["label"],
        milestone_percent=stage["percent"],
        status="requested",
        notes=(payload.notes or "").strip() or None,
        photos=photos or None,
    )
    db.add(draw)
    db.commit()
    db.refresh(draw)
    return _draw_out(draw)


@router.get("/api/quotes/{quote_id}/milestone-draws", response_model=list[schemas.MilestoneDrawOut])
def list_milestone_draws(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The contractor's draw requests + approval status for this quote."""
    quote = _owned_quote(db, current_user, quote_id)
    draws = (
        db.query(models.MilestoneDraw)
        .filter(models.MilestoneDraw.quote_id == quote.id)
        .order_by(models.MilestoneDraw.created_at.desc())
        .all()
    )
    return [_draw_out(d) for d in draws]


@router.get("/milestone/{token}")
def milestone_view(token: str, request: Request, db: Session = Depends(get_db)):
    draw = db.query(models.MilestoneDraw).filter(models.MilestoneDraw.token == token).first()
    if draw is None:
        return templates.TemplateResponse(request, "milestone.html", {"not_found": True})
    quote = db.query(models.Quote).filter(models.Quote.id == draw.quote_id).first()
    if quote is None:
        return templates.TemplateResponse(request, "milestone.html", {"not_found": True})

    org = db.query(models.Organization).filter(models.Organization.id == quote.organization_id).first()
    currency = (org.currency_symbol if org and org.currency_symbol else "$") if org else "$"
    amount = round(float(quote.total or 0) * float(draw.milestone_percent or 0) / 100.0, 2)
    return templates.TemplateResponse(request, "milestone.html", {
        "not_found": False,
        "draw": draw,
        "quote_title": quote.title,
        "site_address": quote.site_address,
        "currency": currency,
        "amount": amount,
        "approved": draw.status == "approved",
    })


@router.post("/milestone/{token}/approve")
def approve_milestone(token: str, db: Session = Depends(get_db)):
    """Homeowner approves the draw: releases the payment stage and records
    the approval timestamp."""
    draw = db.query(models.MilestoneDraw).filter(models.MilestoneDraw.token == token).first()
    if draw is None:
        raise HTTPException(status_code=404, detail="Draw request not found")
    if draw.status == "approved":
        return {"ok": True, "status": "approved", "already": True}
    quote = db.query(models.Quote).filter(models.Quote.id == draw.quote_id).first()
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")

    # Release the stage on the master quote's payment schedule.
    schedule = list(quote.payment_schedule or [])
    if 0 <= draw.milestone_index < len(schedule):
        stage = dict(schedule[draw.milestone_index])
        stage["released"] = True
        schedule[draw.milestone_index] = stage
        quote.payment_schedule = schedule
        db.add(quote)

    draw.status = "approved"
    draw.approved_at = datetime.now(timezone.utc)
    db.add(draw)
    db.commit()
    return {"ok": True, "status": "approved", "amount": float(draw.milestone_percent or 0)}
