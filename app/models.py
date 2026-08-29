"""SQLAlchemy models for the BuildUpQuote user store (multi-tenant).

Organizations are the tenant: a user belongs to at most one organization
(`organization_id` nullable so a freshly-registered user can be added to
an existing organization later via an invite/join flow), and `role` holds
the RBAC level ("owner", "admin", "estimator", "viewer").
"""
import re
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
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
    bio = Column(Text, nullable=True)  # company description / tagline
    phone = Column(String, nullable=True)
    website = Column(String, nullable=True)
    email = Column(String, nullable=True)  # public business contact email
    address = Column(Text, nullable=True)
    license_number = Column(String, nullable=True)  # contractor / trade license
    tax_id = Column(String, nullable=True)  # VAT / EIN / Tax ID
    default_payment_terms = Column(Text, nullable=True)
    currency_symbol = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
    # Master contract / terms of service used on quoted work. Contractors draft
    # it in Settings; a PDF (or DOCX) version can also be attached.
    master_contract_text = Column(Text, nullable=True)
    master_contract_pdf_url = Column(String, nullable=True)
    # Stripe billing / subscriptions. stripe_customer_id links the tenant to
    # its Stripe customer; tier + status are kept in sync by the webhook in
    # app/routers/billing.py. subscription_status is the Stripe state mapped
    # to our vocabulary: trialing / active / past_due / canceled.
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    subscription_tier = Column(String, nullable=False, default="starter")  # starter / pro / enterprise
    subscription_status = Column(String, nullable=False, default="trialing")  # trialing / active / past_due / canceled
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False, default="")
    job_title = Column(String, nullable=True)  # "Owner / Lead Contractor", "Senior Estimator"...
    is_active = Column(Boolean, default=True)
    role = Column(String, default="owner")
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    # Google Contacts sync (People API): the user's OAuth tokens. The refresh
    # token is long-lived; the access token is refreshed on demand.
    google_access_token = Column(String, nullable=True)
    google_refresh_token = Column(String, nullable=True)
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
    calculator = Column(String, nullable=True)  # name of a Python calculator in assembly_calculators.py
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


class TradeSynonym(Base):
    """A raw term a contractor actually types ('sheetrock', '2x4', 'thinset')
    pointing at the canonical TradeCatalogItem it resolves to. raw_term is
    GIN-indexed with trigram operators so PostgreSQL similarity() lookups
    stay fast."""

    __tablename__ = "trade_synonyms"

    id = Column(Integer, primary_key=True, index=True)
    catalog_id = Column(
        Integer, ForeignKey("trade_catalog_items.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    raw_term = Column(String(255), nullable=False, index=True)

    catalog_item = relationship("TradeCatalogItem", back_populates="synonyms")

    __table_args__ = (
        Index(
            "ix_trade_synonyms_raw_term_trgm", "raw_term",
            postgresql_using="gin",
            postgresql_ops={"raw_term": "gin_trgm_ops"},
        ),
    )


class TradeCatalogItem(Base):
    """The canonical trade catalog: a priced material/labor line a
    description can be autocorrected to. 'trade' is the trade category
    (Drywall, Framing, ...), default_trade_type is the display label
    (Material, Labor, Subcontractor, Plant) -- the API lowercases it so the
    quote builder's item_type values (material/labor/plant/subcontractor)
    keep matching."""

    __tablename__ = "trade_catalog_items"

    id = Column(Integer, primary_key=True, index=True)
    trade = Column(String(64), nullable=False, index=True)  # "Drywall", "Carpentry"...
    canonical_name = Column(String(255), nullable=False, unique=True, index=True)
    unit = Column(String(32), nullable=False, default="unit")  # "sheet", "m2", "hour"...
    default_unit_cost = Column(Numeric(10, 2), nullable=False, default=0.0)
    default_trade_type = Column(String(32), nullable=False, default="Material")

    synonyms = relationship(
        "TradeSynonym", back_populates="catalog_item", cascade="all, delete-orphan",
    )


class Quote(Base):
    """A contractor quote -- the tenant-owned container for quote lines."""

    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    title = Column(String, nullable=False, default="Untitled quote")
    site_address = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft / sent / accepted
    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate_percent = Column(Numeric(5, 2), nullable=True)  # flat rate; None = tax not set
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    # Public 1-click approval: an unguessable shareable link plus the digital
    # sign-off record (signature image, signer name, accepted timestamp) and
    # the audit trail that makes it legally defensible (IP, user agent, email).
    public_uuid = Column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    client_signature = Column(Text, nullable=True)  # base64 PNG / data-URI from the signature pad
    signed_by = Column(String, nullable=True)       # name the signer typed
    signer_email = Column(String, nullable=True)
    signer_ip = Column(String, nullable=True)
    signer_user_agent = Column(Text, nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    # Master contract attachment: whether to append the contract terms to this
    # quote's PDF/public link, plus an optional project-specific override.
    include_contract = Column(Boolean, nullable=False, default=True)
    custom_contract_override = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="quotes")
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


class Client(Base):
    """A client of the organization (the "who is this quote for" record)."""

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    site_address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    quotes = relationship("Quote", back_populates="client")

