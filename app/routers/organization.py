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


def _get_org(current_user: models.User) -> models.Organization:
    if current_user.organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No organization",
        )
    return current_user.organization


@router.get("/me", response_model=schemas.OrganizationOut)
def organization_me(current_user: models.User = Depends(get_current_user)):
    """The authenticated user's own organization. 404 when they don't
    belong to one yet (they registered without an organization name)."""
    return _get_org(current_user)


@router.put("/me", response_model=schemas.OrganizationOut)
def update_organization_me(
    payload: schemas.OrganizationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Partial update of the organization profile. Only the fields sent
    are touched; a blank name is rejected."""
    org = _get_org(current_user)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Business name cannot be blank")
        org.name = name
    for field in ("phone", "address", "tax_id", "default_payment_terms", "currency_symbol"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(org, field, value)
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
    org = _get_org(current_user)
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

