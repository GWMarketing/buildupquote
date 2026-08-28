"""The authenticated user's own profile -- name, job title, role."""
from fastapi import APIRouter, Depends

from app import models, schemas
from app.auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me", response_model=schemas.UserProfileOut)
def users_me(current_user: models.User = Depends(get_current_user)):
    """The current user's profile (full_name, job_title, role)."""
    return current_user
