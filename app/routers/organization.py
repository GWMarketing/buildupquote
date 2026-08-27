"""Organization endpoints -- the current tenant (RBAC groundwork)."""
from fastapi import APIRouter, Depends, HTTPException, status

from app import models, schemas
from app.auth import get_current_user

router = APIRouter(prefix="/api/organization", tags=["organization"])


@router.get("/me", response_model=schemas.OrganizationOut)
def organization_me(current_user: models.User = Depends(get_current_user)):
    """The authenticated user's own organization. 404 when they don't
    belong to one yet (they registered without an organization name)."""
    if current_user.organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No organization",
        )
    return current_user.organization
