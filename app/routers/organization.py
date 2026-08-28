"""Organization endpoints -- the current tenant (profile + logo)."""
import os
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/organization", tags=["organization"])

_LOGO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "uploads", "logos"
)
_ALLOWED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


def _unique_slug(db: Session, base: str) -> str:
    """'acme-roofing', 'acme-roofing-2', 'acme-roofing-3' ... -- the first
    candidate that isn't already taken (mirrors auth.register)."""
    candidate, n = base, 2
    exists = db.query(models.Organization).filter(models.Organization.slug == candidate).first()
    while exists:
        candidate = f"{base}-{n}"
        n += 1
        exists = db.query(models.Organization).filter(models.Organization.slug == candidate).first()
    return candidate


def _get_or_provision_org(db: Session, current_user: models.User) -> models.Organization:
    """The user's organization, auto-created on first access so an org-less
    account (registered without a company name) can still manage a profile.
    The name falls back to the user's full name, then 'My Business'."""
    if current_user.organization is not None:
        return current_user.organization
    base_name = (current_user.full_name or "").strip() or "My Business"
    org = models.Organization(
        name=base_name,
        slug=_unique_slug(db, models.slugify(base_name)),
    )
    db.add(org)
    db.flush()
    current_user.organization_id = org.id
    db.add(current_user)
    db.commit()
    db.refresh(org)
    return org


@router.get("/me", response_model=schemas.OrganizationOut)
def organization_me(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The authenticated user's own organization profile. Auto-provisions an
    organization for accounts that registered without one, so every user has
    a profile to manage."""
    return _get_or_provision_org(db, current_user)


@router.put("/me", response_model=schemas.OrganizationOut)
def update_organization_me(
    payload: schemas.OrganizationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Partial update of the organization profile. Only the fields sent are
    touched; a blank name is rejected."""
    org = _get_or_provision_org(db, current_user)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Business name cannot be blank")
        org.name = name
    for field in ("description", "website", "email", "phone", "address",
                  "license_number", "tax_id", "default_payment_terms", "currency_symbol"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(org, field, value.strip() if isinstance(value, str) else value)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@router.post("/logo", response_model=schemas.OrganizationOut)
async def upload_organization_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a new logo (PNG/JPG/SVG/GIF/WebP, max 2MB). Saves it under
    /static/uploads/logos and updates logo_url on the organization."""
    org = _get_or_provision_org(db, current_user)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_LOGO_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Logo must be PNG, JPG, SVG, GIF or WebP",
        )
    raw = await file.read()
    if len(raw) > _MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="Logo too large (max 2MB)")

    os.makedirs(_LOGO_DIR, exist_ok=True)
    filename = f"org-{org.id}-{int(time.time())}{ext}"
    with open(os.path.join(_LOGO_DIR, filename), "wb") as fh:
        fh.write(raw)
    org.logo_url = f"/static/uploads/logos/{filename}"
    db.add(org)
    db.commit()
    db.refresh(org)
    return org

