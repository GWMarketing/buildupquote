"""SQLAlchemy models for the BuildUpQuote user store (multi-tenant).

Organizations are the tenant: a user belongs to at most one organization
(`organization_id` nullable so a freshly-registered user can be added to
an existing organization later via an invite/join flow), and `role` holds
the RBAC level ("owner", "admin", "estimator", "viewer").
"""
import re

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
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


class TradeLexicon(Base):
    """Standard trade terms + the slang/spoken aliases a contractor might
    actually use on an estimate ("sheetrock", "gyprock" ...)."""

    __tablename__ = "trade_lexicon"

    id = Column(Integer, primary_key=True, index=True)
    trade = Column(String, nullable=False, index=True)
    term = Column(String, nullable=False, index=True)
    aliases = Column(JSON, nullable=True)
    default_unit = Column(String, nullable=True)


class ParametricAssembly(Base):
    """A reusable template whose components are priced from formulas.

    organization_id is NULL for the global standard templates; an
    organization can add its own on top."""

    __tablename__ = "parametric_assemblies"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    required_inputs = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    components = relationship(
        "AssemblyComponent",
        back_populates="assembly",
        cascade="all, delete-orphan",
    )


class AssemblyComponent(Base):
    """One priced line inside an assembly. The formula is a plain string
    over the assembly's input dimensions ("length * height * 1.1")."""

    __tablename__ = "assembly_components"

    id = Column(Integer, primary_key=True, index=True)
    assembly_id = Column(
        Integer, ForeignKey("parametric_assemblies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    description = Column(String, nullable=False)
    item_type = Column(String, nullable=False, default="material")  # material/labor/plant/subcontractor
    unit = Column(String, nullable=False)
    formula = Column(String, nullable=False)
    default_unit_cost = Column(Numeric(10, 2), nullable=False)
    default_markup_percent = Column(Numeric(5, 2), default=20.00)

    assembly = relationship("ParametricAssembly", back_populates="components")


class Quote(Base):
    """A contractor quote -- the tenant-owned container for quote lines."""

    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    title = Column(String, nullable=False, default="Untitled quote")
    subtotal = Column(Numeric(12, 2), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship(
        "QuoteLineItem",
        back_populates="quote",
        cascade="all, delete-orphan",
    )


class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    trade = Column(String, nullable=True)
    description = Column(String, nullable=False)
    item_type = Column(String, nullable=False, default="material")
    quantity = Column(Numeric(12, 3), nullable=False, default=0)
    unit = Column(String, nullable=False)
    unit_cost = Column(Numeric(10, 2), nullable=False)
    markup_percent = Column(Numeric(5, 2), default=20.00)
    line_total = Column(Numeric(12, 2), nullable=False, default=0)
    position = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    quote = relationship("Quote", back_populates="items")

