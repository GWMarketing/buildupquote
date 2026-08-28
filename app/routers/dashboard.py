"""Dashboard analytics: one endpoint that powers the /dashboard KPI cards
and the recent-proposals board. All figures are scoped to the caller's
organization; org-less users get a zeroed payload so the page always renders.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ACTIVE_STATUSES = ("draft", "sent")


@router.get("/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """{stats: {currency, pipeline_total, won_revenue, active_quotes_count,
    accepted_quotes_count, win_rate, avg_margin}, recent_quotes: [...]}."""
    empty = {
        "stats": {
            "currency": "£", "pipeline_total": "0.00", "won_revenue": "0.00",
            "active_quotes_count": 0, "accepted_quotes_count": 0,
            "win_rate": 0, "avg_margin": 0,
        },
        "recent_quotes": [],
    }
    if current_user.organization_id is None:
        return empty

    org_id = current_user.organization_id
    quotes = (
        db.query(models.Quote)
        .filter(models.Quote.organization_id == org_id)
        .all()
    )

    pipeline = sum(float(q.total or 0) for q in quotes if q.status in _ACTIVE_STATUSES)
    won = sum(float(q.total or 0) for q in quotes if q.status == "accepted")
    active_count = sum(1 for q in quotes if q.status in _ACTIVE_STATUSES)
    accepted_count = sum(1 for q in quotes if q.status == "accepted")
    sent_count = sum(1 for q in quotes if q.status == "sent")

    # Acceptance conversion: of the proposals that left "draft", how many won.
    decided = accepted_count + sent_count
    win_rate = (accepted_count / decided * 100) if decided else 0

    avg_margin = (
        db.query(func.avg(models.QuoteLineItem.markup_percent))
        .join(models.Quote, models.QuoteLineItem.quote_id == models.Quote.id)
        .filter(models.Quote.organization_id == org_id)
        .scalar()
    )

    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    currency = org.currency_symbol if (org and org.currency_symbol) else "£"

    recent = (
        db.query(models.Quote)
        .filter(models.Quote.organization_id == org_id)
        .order_by(models.Quote.created_at.desc(), models.Quote.id.desc())
        .limit(8)
        .all()
    )
    recent_quotes = [{
        "id": q.id,
        "title": q.title,
        "client_name": q.client.name if q.client else None,
        "status": (q.status or "draft").title(),  # "Draft" / "Sent" / "Accepted"
        "total_amount": round(float(q.total or 0), 2),
    } for q in recent]

    return {
        "stats": {
            "currency": currency,
            "pipeline_total": f"{pipeline:.2f}",
            "won_revenue": f"{won:.2f}",
            "active_quotes_count": active_count,
            "accepted_quotes_count": accepted_count,
            "win_rate": round(win_rate, 1),
            "avg_margin": round(float(avg_margin or 0), 1),
        },
        "recent_quotes": recent_quotes,
    }
