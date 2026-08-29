"""Platform-admin endpoints: cross-tenant visibility + account control.

Everything here is gated behind `get_current_admin`, so only users with the
`is_admin` flag can see or change anything. Responses never include
hashed_password, Google OAuth tokens or Stripe secrets.

What an admin can do:
  - see platform-wide stats, every organization, user and client
  - override any organization's subscription tier/status (manual, no Stripe)
  - promote/demote platform admins, activate/deactivate accounts
  - export the client list as CSV (all orgs, or one org)
"""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import models
from app.auth import get_current_admin
from app.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

_ACTIVE_STATUSES = ("draft", "sent")
TIERS = {"starter", "pro", "enterprise"}
STATUSES = {"trialing", "active", "past_due", "canceled"}
_TIER_MONTHLY = {"starter": 29, "pro": 69, "enterprise": 149}


class SubscriptionOverride(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None


class AdminFlagUpdate(BaseModel):
    is_admin: bool


class ActiveUpdate(BaseModel):
    is_active: bool


def _user_admin_out(user: models.User) -> dict:
    """A user row for the admin console (no hashed password, no tokens)."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "job_title": user.job_title,
        "role": user.role,
        "is_admin": bool(user.is_admin),
        "is_active": bool(user.is_active),
        "organization_id": user.organization_id,
        "organization_name": user.organization.name if user.organization else None,
        "created_at": user.created_at,
    }


def _org_admin_out(db: Session, org: models.Organization) -> dict:
    quote_count = (
        db.query(func.count(models.Quote.id))
        .filter(models.Quote.organization_id == org.id)
        .scalar()
    )
    client_count = (
        db.query(func.count(models.Client.id))
        .filter(models.Client.organization_id == org.id)
        .scalar()
    )
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "email": org.email,
        "subscription_tier": org.subscription_tier,
        "subscription_status": org.subscription_status,
        "trial_ends_at": org.trial_ends_at,
        "quote_count": quote_count or 0,
        "client_count": client_count or 0,
        "user_count": len(org.users),
        "created_at": org.created_at,
    }

@router.get("/stats")
def admin_stats(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Platform-wide overview: counts, revenue, subscription breakdown, and
    the recent signups/quotes behind the numbers."""
    orgs = db.query(models.Organization).options(selectinload(models.Organization.users)).all()
    users = db.query(models.User).count()
    admins = db.query(models.User).filter(models.User.is_admin.is_(True)).count()
    clients = db.query(models.Client).count()
    quotes = db.query(models.Quote).all()

    pipeline = sum(float(q.total or 0) for q in quotes if q.status in _ACTIVE_STATUSES)
    won = sum(float(q.total or 0) for q in quotes if q.status == "accepted")

    breakdown = {"trialing": 0, "active": 0, "past_due": 0, "canceled": 0}
    mrr = 0.0
    for org in orgs:
        st = (org.subscription_status or "trialing").lower()
        if st not in breakdown:
            st = "trialing"
        breakdown[st] += 1
        if st in ("trialing", "active"):
            mrr += _TIER_MONTHLY.get((org.subscription_tier or "starter").lower(), 29)

    avg_margin = db.query(func.avg(models.QuoteLineItem.markup_percent)).scalar()

    recent_signups = (
        db.query(models.User)
        .order_by(models.User.created_at.desc(), models.User.id.desc())
        .limit(5)
        .all()
    )
    recent_quotes = (
        db.query(models.Quote)
        .options(selectinload(models.Quote.client))
        .order_by(models.Quote.created_at.desc(), models.Quote.id.desc())
        .limit(8)
        .all()
    )

    return {
        "counts": {
            "organizations": len(orgs),
            "users": users,
            "admins": admins,
            "clients": clients,
            "quotes": len(quotes),
        },
        "revenue": {
            "pipeline_total": round(pipeline, 2),
            "won_revenue": round(won, 2),
            "avg_margin": round(float(avg_margin or 0), 1),
            "mrr": round(mrr, 2),
        },
        "subscription_breakdown": breakdown,
        "recent_signups": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "created_at": u.created_at,
            }
            for u in recent_signups
        ],
        "recent_quotes": [
            {
                "id": q.id,
                "title": q.title,
                "client_name": q.client.name if q.client else None,
                "status": (q.status or "draft").title(),
                "total_amount": round(float(q.total or 0), 2),
                "organization_id": q.organization_id,
            }
            for q in recent_quotes
        ],
    }

@router.get("/organizations")
def list_organizations(
    search: Optional[str] = None,
    tier: Optional[str] = None,
    org_status: Optional[str] = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Every organization with admin-relevant counts. Filters: ?search=
    (name/slug/email), ?tier=, ?org_status=."""
    query = db.query(models.Organization).options(selectinload(models.Organization.users))
    if search:
        like = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(models.Organization.name).like(like)
            | func.lower(models.Organization.slug).like(like)
            | func.lower(func.coalesce(models.Organization.email, "")).like(like)
        )
    if tier:
        query = query.filter(models.Organization.subscription_tier == tier.strip().lower())
    if org_status:
        query = query.filter(models.Organization.subscription_status == org_status.strip().lower())
    orgs = (
        query.order_by(models.Organization.created_at.desc(), models.Organization.id.desc())
        .all()
    )
    return [_org_admin_out(db, org) for org in orgs]


@router.get("/organizations/{org_id}")
def organization_detail(
    org_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """One organization: profile, its users, its recent quotes."""
    org = (
        db.query(models.Organization)
        .options(selectinload(models.Organization.users))
        .filter(models.Organization.id == org_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    recent_quotes = (
        db.query(models.Quote)
        .filter(models.Quote.organization_id == org.id)
        .order_by(models.Quote.created_at.desc(), models.Quote.id.desc())
        .limit(10)
        .all()
    )
    return {
        **_org_admin_out(db, org),
        "bio": org.bio,
        "phone": org.phone,
        "website": org.website,
        "address": org.address,
        "license_number": org.license_number,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_admin": bool(u.is_admin),
                "is_active": bool(u.is_active),
                "created_at": u.created_at,
            }
            for u in org.users
        ],
        "recent_quotes": [
            {
                "id": q.id,
                "title": q.title,
                "client_name": q.client.name if q.client else None,
                "status": (q.status or "draft").title(),
                "total_amount": round(float(q.total or 0), 2),
                "created_at": q.created_at,
            }
            for q in recent_quotes
        ],
    }


@router.patch("/organizations/{org_id}/subscription")
def override_subscription(
    org_id: int,
    payload: SubscriptionOverride,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Admin manually changes a client account's tier/status. This writes the
    DB directly; it does not create or edit a Stripe subscription."""
    org = (
        db.query(models.Organization)
        .options(selectinload(models.Organization.users))
        .filter(models.Organization.id == org_id)
        .first()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if payload.tier is not None:
        tier = payload.tier.strip().lower()
        if tier not in TIERS:
            raise HTTPException(status_code=422, detail="tier must be starter, pro or enterprise")
        org.subscription_tier = tier
    if payload.status is not None:
        org_status = payload.status.strip().lower()
        if org_status not in STATUSES:
            raise HTTPException(status_code=422, detail="status must be trialing, active, past_due or canceled")
        org.subscription_status = org_status

    db.add(org)
    db.commit()
    db.refresh(org)
    return _org_admin_out(db, org)

@router.get("/users")
def list_users(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Every user across all organizations. Filters: ?search= (email/name)."""
    query = db.query(models.User).options(selectinload(models.User.organization))
    if search:
        like = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(models.User.email).like(like)
            | func.lower(func.coalesce(models.User.full_name, "")).like(like)
        )
    users = (
        query.order_by(models.User.created_at.desc(), models.User.id.desc())
        .all()
    )
    return [_user_admin_out(u) for u in users]


@router.patch("/users/{user_id}/admin")
def set_user_admin(
    user_id: int,
    payload: AdminFlagUpdate,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
):
    """Promote or demote a user's platform-admin flag. You can't demote
    yourself (that would be the last lock on the door)."""
    user = (
        db.query(models.User)
        .options(selectinload(models.User.organization))
        .filter(models.User.id == user_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_admin.id and not payload.is_admin:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")
    user.is_admin = payload.is_admin
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_admin_out(user)


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: int,
    payload: ActiveUpdate,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
):
    """Enable or disable a user's account. Disabled accounts are blocked by
    get_current_user on their next request (even with a live JWT). You can't
    disable yourself."""
    user = (
        db.query(models.User)
        .options(selectinload(models.User.organization))
        .filter(models.User.id == user_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_admin.id and not payload.is_active:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    user.is_active = payload.is_active
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_admin_out(user)


@router.get("/clients")
def list_clients(
    organization_id: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """Every client across all organizations, with org name + quoting stats."""
    query = db.query(models.Client)
    if organization_id:
        query = query.filter(models.Client.organization_id == organization_id)
    if search:
        like = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(models.Client.name).like(like)
            | func.lower(func.coalesce(models.Client.email, "")).like(like)
        )
    clients = query.order_by(models.Client.organization_id, models.Client.name).all()

    quote_counts = dict(
        db.query(models.Quote.client_id, func.count(models.Quote.id))
        .filter(models.Quote.client_id.isnot(None))
        .group_by(models.Quote.client_id)
        .all()
    )
    total_quoted = dict(
        db.query(models.Quote.client_id, func.sum(models.Quote.total))
        .filter(models.Quote.client_id.isnot(None))
        .group_by(models.Quote.client_id)
        .all()
    )
    org_names = {o.id: o.name for o in db.query(models.Organization).all()}

    return [
        {
            "id": c.id,
            "organization_id": c.organization_id,
            "organization_name": org_names.get(c.organization_id, ""),
            "name": c.name,
            "site_address": c.site_address,
            "phone": c.phone,
            "email": c.email,
            "quote_count": quote_counts.get(c.id, 0),
            "total_quoted": round(float(total_quoted.get(c.id, 0) or 0), 2),
            "created_at": c.created_at,
        }
        for c in clients
    ]


@router.get("/clients/export")
def export_clients(
    organization_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_admin),
):
    """All clients as a CSV, or one organization's clients with
    ?organization_id=. Columns: organization, client name, site address,
    phone, email, created_at, quote count, total quoted."""
    query = db.query(models.Client)
    if organization_id:
        query = query.filter(models.Client.organization_id == organization_id)
    clients = query.order_by(models.Client.organization_id, models.Client.name).all()

    quote_counts = dict(
        db.query(models.Quote.client_id, func.count(models.Quote.id))
        .filter(models.Quote.client_id.isnot(None))
        .group_by(models.Quote.client_id)
        .all()
    )
    total_quoted = dict(
        db.query(models.Quote.client_id, func.sum(models.Quote.total))
        .filter(models.Quote.client_id.isnot(None))
        .group_by(models.Quote.client_id)
        .all()
    )
    org_names = {o.id: o.name for o in db.query(models.Organization).all()}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "organization", "client_name", "site_address", "phone", "email",
        "created_at", "quote_count", "total_quoted",
    ])
    for client in clients:
        writer.writerow([
            org_names.get(client.organization_id, ""),
            client.name,
            client.site_address or "",
            client.phone or "",
            client.email or "",
            client.created_at.strftime("%Y-%m-%d") if client.created_at else "",
            quote_counts.get(client.id, 0),
            round(float(total_quoted.get(client.id, 0) or 0), 2),
        ])

    filename = "buildupquote_clients.csv"
    if organization_id:
        filename = f"buildupquote_clients_org_{organization_id}.csv"
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



