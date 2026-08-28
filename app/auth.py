"""Password hashing + JWT utilities for BuildUpQuote.

Hashing is pwdlib (argon2 -- the modern default; passlib[bcrypt] would work
the same way, but argon2 is the stronger choice). Tokens are python-jose.
SECRET_KEY comes from the environment; the fallback is for LOCAL DEV ONLY
and should be overridden on the VPS via the web service's environment.

Also verifies Google Identity Services ID tokens (Google Sign-In): the
frontend hands us a signed credential, we check its signature against
Google's public keys (JWKS), its `aud` against GOOGLE_CLIENT_ID, and then
create/find the user.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwk, jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session, selectinload

from app import models
from app.database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", "buildupquote-dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Google Sign-In (GIS). Empty/unset disables the feature: the auth pages
# hide the button and /api/auth/google answers 503 "not configured". Set the
# client ID from a Google Cloud Console "Web application" OAuth client.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_google_jwks_cache = {"fetched_at": 0.0, "keys": None}

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


def _google_jwks() -> dict:
    """Google's signing keys, cached for an hour. A fetch failure raises
    ValueError so callers turn it into a clean 401."""
    now = time.time()
    if _google_jwks_cache["keys"] is None or now - _google_jwks_cache["fetched_at"] > 3600:
        try:
            with urllib.request.urlopen(_GOOGLE_JWKS_URL, timeout=10) as resp:
                keys = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ValueError("Could not reach Google's signing-key endpoint") from exc
        _google_jwks_cache["keys"] = keys
        _google_jwks_cache["fetched_at"] = now
    return _google_jwks_cache["keys"]


def verify_google_credential(credential: str) -> dict:
    """Verify a Google Identity Services ID token.

    Checks the RS256 signature against Google's public JWKS, the `aud`
    against GOOGLE_CLIENT_ID, and returns {sub, email, email_verified, name}.
    Raises ValueError with a user-facing message on any failure.
    """
    if not credential:
        raise ValueError("Missing Google credential")
    if not GOOGLE_CLIENT_ID:
        raise ValueError("Google sign-in is not configured")
    try:
        headers = jwt.get_unverified_headers(credential)
        key = next(
            (k for k in _google_jwks().get("keys", []) if k.get("kid") == headers.get("kid")),
            None,
        )
        if key is None:
            raise ValueError("Unknown Google signing key")
        claims = jwt.decode(
            credential,
            jwk.construct(key),
            algorithms=["RS256"],
            audience=GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid Google credential") from exc

    email = claims.get("email")
    if not email:
        raise ValueError("Google account has no email address")
    return {
        "sub": claims.get("sub"),
        "email": email,
        "email_verified": bool(claims.get("email_verified")),
        "name": claims.get("name") or "",
    }
