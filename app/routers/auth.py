"""Registration, login, and the authenticated /me endpoint."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    """Emails are stored and matched lowercased, so "A@B.com" and
    "a@b.com" can never become two accounts."""
    return email.strip().lower()


def _unique_slug(db, base: str) -> str:
    """'acme-roofing', 'acme-roofing-2', 'acme-roofing-3' ... -- the first
    candidate that isn't already taken."""
    candidate, n = base, 2
    exists = db.query(models.Organization).filter(models.Organization.slug == candidate).first()
    while exists:
        candidate = f"{base}-{n}"
        n += 1
        exists = db.query(models.Organization).filter(models.Organization.slug == candidate).first()
    return candidate


@router.post("/register", response_model=schemas.RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """Create an account. 400 if the email is already registered.

    When `organization_name` is provided the first sign-up also creates
    the Organization and this user becomes its "owner" (RBAC). Without it
    the user registers org-less and can be attached to an organization
    later."""
    email = _normalize_email(user.email)
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    organization = None
    org_name = (user.organization_name or "").strip()
    if org_name:
        organization = models.Organization(
            name=org_name,
            slug=_unique_slug(db, models.slugify(org_name)),
        )
        db.add(organization)
        db.flush()  # assign organization.id before the user row references it

    db_user = models.User(
        email=email,
        hashed_password=get_password_hash(user.password),
        full_name=user.full_name,
        organization_id=organization.id if organization else None,
        role="owner",
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)  # pull created_at from the server_default
    # Issue a JWT immediately so the UI can land on /dashboard without a
    # separate login round-trip.
    token = create_access_token({"sub": db_user.email})
    return {"user": db_user, "access_token": token, "token_type": "bearer"}


@router.post("/token")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow. The /docs "Authorize" button posts here
    (username = email, password = password) and gets a bearer token."""
    user = db.query(models.User).filter(
        models.User.email == _normalize_email(form.username)
    ).first()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=schemas.UserOut)
def read_me(current_user: models.User = Depends(get_current_user)):
    """The authenticated user's own profile."""
    return current_user
