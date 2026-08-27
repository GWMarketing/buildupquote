"""Password hashing + JWT utilities for BuildUpQuote.

Hashing is pwdlib (argon2 -- the modern default; passlib[bcrypt] would work
the same way, but argon2 is the stronger choice). Tokens are python-jose.
SECRET_KEY comes from the environment; the fallback is for LOCAL DEV ONLY
and should be overridden on the VPS via the web service's environment.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session, selectinload

from app import models
from app.database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", "buildupquote-dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

_password_hash = PasswordHash.recommended()

# tokenUrl must point at the login endpoint; this is also what puts the
# green "Authorize" button on /docs.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_password_hash(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return _password_hash.verify(plain_password, hashed_password)
    except Exception:  # noqa: BLE001 -- pwdlib raises UnknownHashError for
        return False  # anything that isn't a hash it recognizes


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """A signed JWT whose payload carries `data` plus an exp claim.

    Default lifetime is ACCESS_TOKEN_EXPIRE_MINUTES; pass expires_delta to
    override per-token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None
        else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """FastAPI dependency for protected endpoints: decode the bearer token,
    load the matching user, or 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    # selectinload populates current_user.organization eagerly, so tenant
    # endpoints (e.g. /api/organization/me) work without a lazy-load round
    # trip and can't blow up after the session's scope.
    user = (
        db.query(models.User)
        .options(selectinload(models.User.organization))
        .filter(models.User.email == email)
        .first()
    )
    if user is None:
        raise credentials_exception
    return user
