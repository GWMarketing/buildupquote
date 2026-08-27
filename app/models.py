"""SQLAlchemy models for the BuildUpQuote user store (multi-tenant).

Organizations are the tenant: a user belongs to at most one organization
(`organization_id` nullable so a freshly-registered user can be added to
an existing organization later via an invite/join flow), and `role` holds
the RBAC level ("owner", "admin", "estimator", "viewer").
"""
import re

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.database import Base


def slugify(name: str) -> str:
    """"Glenn's Roofing & Co" -> "glenns-roofing-co". Falls back to a
    generic slug if the name is entirely punctuation."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return cleaned or "organization"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    role = Column(String, default="owner")
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="users")

