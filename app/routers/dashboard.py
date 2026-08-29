"""Dashboard analytics: one endpoint that powers the /dashboard KPI cards
and the recent-proposals board. All figures are scoped to the caller's
organization; org-less users get a zeroed payload so the page always renders.

?range=all|month|week narrows every figure (and the recent list) to quotes
created within the window, so the dashboard's time selector actually filters.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ACTIVE_STATUSES = ("draft", "sent")
_RANGE_DAYS = {"week": 7, "month": 30, "all": None}


@router.get("/stats")
def dashboard_stats(
    range: str = "all",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """{stats: {currency, open_quotes_count, open_quotes_total,
    active_jobs_count, won_revenue, pending_deposits_count,
    pending_deposits_total, win_rate, avg_margin}, recent_quotes: [...]}."""
    empty = {
        "stats": {
            "currency": "$", "open_quotes_count": 0, "open_quotes_total": "0.00",
            "active_jobs_count": 0, "won_revenue": "0.00",
            "pending_deposits_count": 0, "pending_deposits_total": "0.00",
            "win_rate": 0, "avg_margin": 0,
            # Backwards-compatible aliases.
            "pipeline_total": "0.00", "active_quotes_count": 0,
            "accepted_quotes_count": 0,
        },
        "recent_quotes": [],
    }
    if current_user.organization_id is None:
        return empty

    org_id = current_user.organization_id
    days = _RANGE_DAYS.get((range or "all").lower(), None)
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    q = db.query(models.Quote).filter(models.Quote.organization_id == org_id)
    if cutoff:
        q = q.filter(models.Quote.created_at >= cutoff)
    quotes = q.all()

    pipeline = sum(float(x.total or 0) for x in quotes if x.status in _ACTIVE_STATUSES)
    won = sum(float(x.total or 0) for x in quotes if x.status == "accepted")
    open_count = sum(1 for x in quotes if x.status in _ACTIVE_STATUSES)
    jobs_count = sum(1 for x in quotes if x.status == "accepted")
    sent_count = sum(1 for x in quotes if x.status == "sent")

    # Pending deposits: accepted jobs whose first (deposit) milestone has not
    # been released on the payment schedule.
    pending_deposits_count = 0
    pending_deposits_total = 0.0
    for x in quotes:
        if x.status != "accepted":
            continue
        schedule = x.payment_schedule or []
        if not schedule:
            continue
        first = schedule[0]
        if first.get("released"):
            continue
        pending_deposits_count += 1
        pending_deposits_total += float(x.total or 0) * float(first.get("percent") or 0) / 100.0

    decided = jobs_count + sent_count
    win_rate = (jobs_count / decided * 100) if decided else 0

    avg_margin = (
        db.query(func.avg(models.QuoteLineItem.markup_percent))
        .join(models.Quote, models.QuoteLineItem.quote_id == models.Quote.id)
        .filter(models.Quote.organization_id == org_id)
        .scalar()
    )

    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    currency = org.currency_symbol if (org and org.currency_symbol) else "$"

    recent = (
        q.order_by(models.Quote.created_at.desc(), models.Quote.id.desc())
        .limit(8)
        .all()
    )
    recent_quotes = [{
        "id": x.id,
        "title": x.title,
        "client_name": x.client.name if x.client else None,
        "status": (x.status or "draft").title(),  # "Draft" / "Sent" / "Accepted"
        "total_amount": round(float(x.total or 0), 2),
        "created_at": x.created_at,
    } for x in recent]

    return {
        "stats": {
            "currency": currency,
            "open_quotes_count": open_count,
            "open_quotes_total": f"{pipeline:.2f}",
            "active_jobs_count": jobs_count,
            "won_revenue": f"{won:.2f}",
            "pending_deposits_count": pending_deposits_count,
            "pending_deposits_total": f"{pending_deposits_total:.2f}",
            "win_rate": round(win_rate, 1),
            "avg_margin": round(float(avg_margin or 0), 1),
            # Backwards-compatible aliases.
            "pipeline_total": f"{pipeline:.2f}",
            "active_quotes_count": open_count,
            "accepted_quotes_count": jobs_count,
        },
        "recent_quotes": recent_quotes,
    }

