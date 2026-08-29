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


def _profile_out(org: models.Organization, user: models.User) -> dict:
    """The combined business + representative profile returned by /me."""
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "bio": org.bio,
        "website": org.website,
        "email": org.email,
        "phone": org.phone,
        "address": org.address,
        "license_number": org.license_number,
        "tax_id": org.tax_id,
        "default_payment_terms": org.default_payment_terms,
        "currency_symbol": org.currency_symbol,
        "logo_url": org.logo_url,
        "master_contract_text": org.master_contract_text,
        "master_contract_pdf_url": org.master_contract_pdf_url,
        "stripe_customer_id": org.stripe_customer_id,
        "stripe_subscription_id": org.stripe_subscription_id,
        "subscription_tier": org.subscription_tier,
        "subscription_status": org.subscription_status,
        "trial_ends_at": org.trial_ends_at,
        "created_at": org.created_at,
        "full_name": user.full_name,
        "job_title": user.job_title,
    }


@router.get("/me", response_model=schemas.OrganizationProfileOut)
def organization_me(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The authenticated user's full business profile plus their own name and
    job title. Auto-provisions an organization for accounts that registered
    without one, so every user has a profile to manage."""
    org = _get_or_provision_org(db, current_user)
    return _profile_out(org, current_user)


@router.put("/me", response_model=schemas.OrganizationProfileOut)
def update_organization_me(
    payload: schemas.OrganizationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Partial update of the business profile and the representative. Only the
    fields sent are touched; a blank name is rejected."""
    org = _get_or_provision_org(db, current_user)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Business name cannot be blank")
        org.name = name
    for field in ("bio", "website", "email", "phone", "address",
                  "license_number", "tax_id", "default_payment_terms", "currency_symbol",
                  "master_contract_text"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(org, field, value.strip() if isinstance(value, str) else value)
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()
    if payload.job_title is not None:
        current_user.job_title = payload.job_title.strip()
    db.add(org)
    db.add(current_user)
    db.commit()
    db.refresh(org)
    db.refresh(current_user)
    return _profile_out(org, current_user)


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


_CONTRACT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "uploads", "contracts"
)
_ALLOWED_CONTRACT_EXTS = {".pdf", ".docx"}
_MAX_CONTRACT_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post("/contract-file", response_model=schemas.OrganizationProfileOut)
async def upload_organization_contract(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a master contract file (PDF or DOCX, max 20MB). Saves it under
    /static/uploads/contracts and records the URL on the organization."""
    org = _get_or_provision_org(db, current_user)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_CONTRACT_EXTS:
        raise HTTPException(status_code=400, detail="Contract file must be a PDF or DOCX")
    raw = await file.read()
    if len(raw) > _MAX_CONTRACT_BYTES:
        raise HTTPException(status_code=400, detail="Contract file too large (max 20MB)")

    os.makedirs(_CONTRACT_DIR, exist_ok=True)
    filename = f"contract-{org.id}-{int(time.time())}{ext}"
    with open(os.path.join(_CONTRACT_DIR, filename), "wb") as fh:
        fh.write(raw)
    org.master_contract_pdf_url = f"/static/uploads/contracts/{filename}"
    db.add(org)
    db.commit()
    db.refresh(org)
    return _profile_out(org, current_user)

