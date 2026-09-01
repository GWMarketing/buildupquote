"""Crew roster + availability calendar.

Crew members are real User accounts with role="crew" and no office access
(the middleware in fastapi_app.py blocks them from every /api path except
their own crew endpoints + profile). They log in with a temporary password
issued by their builder (the BC), then mark their own availability on a
month calendar. The builder sees every member's calendar combined.
"""
import os
import re
import secrets
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, get_password_hash
from app.database import get_db
from app.services import email_service

router = APIRouter(prefix="/api/crew", tags=["crew"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MANAGER_ROLES = ("owner", "admin", "estimator")


def _is_manager(user: models.User) -> bool:
    return user.role in _MANAGER_ROLES


def _require_manager(user: models.User) -> None:
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Only the builder can manage the crew roster")


def _member_out(user: models.User):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "trade": user.trade,
        "role": user.role,
        "is_active": user.is_active,
        "organization_id": user.organization_id,
    }


def _parse_month(month: str) -> tuple[int, int]:
    """'2026-09' -> (2026, 9), or a 400 if malformed / too far from today."""
    if not _MONTH_RE.match(month or ""):
        raise HTTPException(status_code=400, detail="month must look like YYYY-MM")
    year, mon = int(month[:4]), int(month[5:])
    if abs((datetime.now().year - year) * 12 + datetime.now().month - mon) > 24:
        raise HTTPException(status_code=400, detail="month is too far in the past or future")
    return year, mon


def _month_days(month: str) -> list[date]:
    year, mon = _parse_month(month)
    import calendar

    last = calendar.monthrange(year, mon)[1]
    return [date(year, mon, d) for d in range(1, last + 1)]


def _availability_map(db: Session, user_id: int, month: str) -> dict[str, str]:
    year, mon = _parse_month(month)
    import calendar

    last = calendar.monthrange(year, mon)[1]
    rows = (
        db.query(models.CrewAvailability)
        .filter(
            models.CrewAvailability.user_id == user_id,
            models.CrewAvailability.date >= date(year, mon, 1),
            models.CrewAvailability.date <= date(year, mon, last),
        )
        .all()
    )
    return {row.date.strftime("%Y-%m-%d"): row.status for row in rows}


def _save_availability(db: Session, user_id: int, payload: schemas.AvailabilityIn) -> None:
    valid_days = set(d.isoformat() for d in _month_days(payload.month))
    for day, status in payload.days.items():
        if day not in valid_days:
            raise HTTPException(status_code=400, detail=f"{day} is not in {payload.month}")
        if status not in ("available", "unavailable", "unset"):
            raise HTTPException(status_code=400, detail=f"bad status {status!r} for {day}")
        d = date.fromisoformat(day)
        row = (
            db.query(models.CrewAvailability)
            .filter(
                and_(
                    models.CrewAvailability.user_id == user_id,
                    models.CrewAvailability.date == d,
                )
            )
            .first()
        )
        if status == "unset":
            if row:
                db.delete(row)
        elif row:
            row.status = status
        else:
            db.add(models.CrewAvailability(user_id=user_id, date=d, status=status))
    db.commit()


def _owned_member(db: Session, manager: models.User, user_id: int) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if (
        user is None
        or user.role != "crew"
        or user.organization_id != manager.organization_id
    ):
        raise HTTPException(status_code=404, detail="Crew member not found")
    return user


# ---------------------------------------------------------------------------
# Crew self-service endpoints -- only the member's own record, always.
# Declared before the /{user_id} routes so "me" is never parsed as an id.
# ---------------------------------------------------------------------------
@router.get("/me")
def crew_me(current_user: models.User = Depends(get_current_user)):
    """The logged-in crew member's own profile."""
    if current_user.role != "crew":
        raise HTTPException(status_code=403, detail="Not a crew account")
    return _member_out(current_user)


@router.get("/me/availability", response_model=schemas.AvailabilityOut)
def crew_my_availability(
    month: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "crew":
        raise HTTPException(status_code=403, detail="Not a crew account")
    _month_days(month)
    return {"month": month, "days": _availability_map(db, current_user.id, month)}


@router.put("/me/availability", response_model=schemas.AvailabilityOut)
def set_my_availability(
    payload: schemas.AvailabilityIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Crew self-service: mark their own days available/unavailable."""
    if current_user.role != "crew":
        raise HTTPException(status_code=403, detail="Not a crew account")
    _save_availability(db, current_user.id, payload)
    return {"month": payload.month, "days": _availability_map(db, current_user.id, payload.month)}


# ---------------------------------------------------------------------------
# Builder (manager) endpoints -- the roster and full combined picture.
# ---------------------------------------------------------------------------
@router.get("", response_model=list[schemas.CrewMemberOut])
def list_crew(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """All crew members in the builder's organization."""
    _require_manager(current_user)
    if not current_user.organization_id:
        return []
    return (
        db.query(models.User)
        .filter(
            models.User.organization_id == current_user.organization_id,
            models.User.role == "crew",
        )
        .order_by(models.User.full_name)
        .all()
    )


@router.post("", response_model=schemas.CrewMemberCreatedOut, status_code=201)
def create_crew_member(
    payload: schemas.CrewMemberCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add a crew member: creates a role='crew' account with a temporary
    password and emails them their login credentials."""
    _require_manager(current_user)
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="Your account has no organization yet")
    email = payload.email.strip().lower()
    if not payload.full_name.strip():
        raise HTTPException(status_code=400, detail="full_name is required")
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="A user with that email already exists")

    temp_password = secrets.token_urlsafe(9)
    member = models.User(
        email=email,
        hashed_password=get_password_hash(temp_password),
        full_name=payload.full_name.strip(),
        phone=(payload.phone or "").strip() or None,
        trade=(payload.trade or "").strip() or None,
        role="crew",
        organization_id=current_user.organization_id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    org_name = (current_user.organization.name if current_user.organization else "") or "your team"
    email_service.send_email(
        to_email=email,
        subject=f"You've been added to {org_name} on BuildUpQuote",
        html_body=(
            f"<p>Hi {member.full_name or 'there'},</p>"
            f"<p><strong>{current_user.full_name or 'Your builder'}</strong> added you to "
            f"<strong>{org_name}</strong> on BuildUpQuote.</p>"
            f"<p>Log in to mark your availability for upcoming jobs:</p>"
            f"<p>Email: <code>{email}</code><br>"
            f"Temporary password: <code>{temp_password}</code></p>"
            f"<p><a href=\"{os.getenv('PUBLIC_BASE_URL', 'https://glennwestman.com')}/login\">"
            f"Open BuildUpQuote</a></p>"
            f"<p>You can change this password any time in Settings after logging in.</p>"
        ),
    )
    return {**_member_out(member), "temporary_password": temp_password}


@router.patch("/{user_id}", response_model=schemas.CrewMemberOut)
def update_crew_member(
    user_id: int,
    payload: schemas.CrewMemberUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Edit a crew member's name, trade, phone, or active flag."""
    _require_manager(current_user)
    member = _owned_member(db, current_user, user_id)
    if payload.full_name is not None:
        member.full_name = payload.full_name.strip() or member.full_name
    if payload.phone is not None:
        member.phone = payload.phone.strip() or None
    if payload.trade is not None:
        member.trade = payload.trade.strip() or None
    if payload.is_active is not None:
        member.is_active = payload.is_active
    db.add(member)
    db.commit()
    db.refresh(member)
    return _member_out(member)


@router.delete("/{user_id}")
def deactivate_crew_member(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Remove a crew member. The account is deactivated (login blocked) but
    its availability history is kept for audit."""
    _require_manager(current_user)
    member = _owned_member(db, current_user, user_id)
    member.is_active = False
    db.add(member)
    db.commit()
    return {"ok": True, "id": user_id}


@router.get("/{user_id}/availability", response_model=schemas.AvailabilityOut)
def get_member_availability(
    user_id: int,
    month: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """A member's month map, as seen by the builder (override allowed)."""
    _require_manager(current_user)
    member = _owned_member(db, current_user, user_id)
    _month_days(month)
    return {"month": month, "days": _availability_map(db, member.id, month)}


@router.put("/{user_id}/availability", response_model=schemas.AvailabilityOut)
def set_member_availability(
    user_id: int,
    payload: schemas.AvailabilityIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Builder override: mark a member's days for them."""
    _require_manager(current_user)
    member = _owned_member(db, current_user, user_id)
    _save_availability(db, member.id, payload)
    return {"month": payload.month, "days": _availability_map(db, member.id, payload.month)}


