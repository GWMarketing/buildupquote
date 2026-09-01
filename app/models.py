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
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
    # Crew roster: phone + trade/role (e.g. "Framing", "Drywall", "Apprentice")
    # so the availability calendar doubles as a working crew list.
    phone = Column(String, nullable=True)
    trade = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    role = Column(String, default="owner")
    # Platform admin (full-instance access). Separate axis from the org-scoped
    # `role` column: an admin can see/control every organization regardless of
    # which org they belong to. Only admins can set it.
    is_admin = Column(Boolean, nullable=False, default=False)
    # Profit guard: warn in the quote cockpit when the gross margin drops
    # below this contractor's minimum threshold (null = no warning).
    min_margin_percent = Column(Numeric(5, 2), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    # Google Contacts sync (People API): the user's OAuth tokens. The refresh
    # token is long-lived; the access token is refreshed on demand.
    google_access_token = Column(String, nullable=True)
    google_refresh_token = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="users")


class CrewAvailability(Base):
    """One row per crew member per date they've marked availability.

    status is 'available' or 'unavailable'; an absent row means the day is
    simply unmarked. The (user_id, date) pair is unique so toggling a day is
    an upsert, and a member's whole month is one cheap query."""

    __tablename__ = "crew_availability"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_crew_availability_user_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    date = Column(Date, nullable=False, index=True)
    status = Column(String, nullable=False, default="available")  # available / unavailable
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TradeLexicon(Base):
    """Standard trade terms + the slang/spoken aliases a contractor might
    actually use on an estimate ("sheetrock", "gyprock" ...).

    The rich fields (phonetic respelling, IPA, misspellings, definition)
    power the voice STT normalizer and the builder's autocomplete; they are
    populated by app/seeds/lexicon (300+ entries per trade, idempotent)."""

    __tablename__ = "trade_lexicon"

    # (trade, term) is the seed's natural key. The UNIQUE index was added
    # for production (deploy #92's 2x-lexicon bug); new databases get it
    # from create_all, existing ones from app.seeds._upsert.ensure_*().
    __table_args__ = (
        UniqueConstraint("trade", "term", name="uq_trade_lexicon_trade_term"),
    )

    id = Column(Integer, primary_key=True, index=True)
    trade = Column(String, nullable=False, index=True)
    term = Column(String, nullable=False, index=True)
    aliases = Column(JSON, nullable=True)
    default_unit = Column(String, nullable=True)
    # ---- Rich lexicon fields (voice STT + manual autocomplete) ----
    uuid = Column(String(36), unique=True, index=True)
    phonetic_respelling = Column(Text, nullable=True)
    ipa_pronunciation = Column(Text, nullable=True)
    common_misspellings_typos = Column(JSON, nullable=True)
    definition_and_use = Column(Text, nullable=True)
    # Lowercased corpus of canonical term + aliases + misspellings for
    # full-text search. The Postgres migration/DDL makes this a tsvector
    # GIN index; SQLite (tests) uses it as a plain contains target.
    search_vector = Column(Text, nullable=True)


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
    unit = Column(String(32), nullable=False, default="unit")  # "sheet", "sq ft", "hour"...
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
    # Payment milestones: [{"label": "Deposit upon signing", "percent": 50}, ...]
    # Built in the quote builder, rendered on the public proposal page + PDF.
    payment_schedule = Column(JSON, nullable=True)
    # Standard exclusions (trade legal safeguards): display strings picked in
    # the builder's "Standard Exclusions" panel, rendered verbatim as a scope
    # exclusions section on the public proposal page and PDF.
    exclusions = Column(JSON, nullable=True)
    # Warranty & guarantee clauses (display strings), rendered as a
    # "Warranty & Guarantee" section on the public proposal and PDF.
    warranty_terms = Column(JSON, nullable=True)
    # Post-sign deposit prompt: payment instructions for the client --
    # {"payment_link": "https://...", "venmo": "handle", "bank_wire": "..."}.
    payment_instructions = Column(JSON, nullable=True)
    # Change orders: an accepted quote can spawn child quotes (CO1, CO2...)
    # that inherit client/project info but carry their own scope + sign-off.
    parent_quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=True, index=True)
    change_order_code = Column(String, nullable=True)
    # Contingency / unforeseen-conditions buffer (0-20%). Applied to the
    # project subtotal and added to the total. contingency_visible controls
    # whether a "Contingency Reserve" line is shown to the client.
    contingency_percent = Column(Numeric(5, 2), nullable=False, default=0)
    contingency_visible = Column(Boolean, nullable=False, default=False)
    # Expiration window: how many days the pricing is valid (null = no
    # expiry). The public proposal page disables signing after expiry.
    expiration_days = Column(Integer, nullable=True, default=14)
    # The room dimensions last used to scope this quote -- shown on the crew
    # work order (sanitized print view).
    scope_dimensions = Column(JSON, nullable=True)
    # Batch room replicator: every applied assembly is recorded as a room --
    # [{key, name, assembly_code, dimensions, waste_percent, line_ids}] -- so
    # the contractor can duplicate a room with new dimensions.
    rooms = Column(JSON, nullable=True)
    # Flat municipal permit / city fee, added straight to the grand total with
    # no margin markup. tax_rate_percent is the MATERIAL tax rate: applied to
    # the post-waste material subtotal only (labor stays tax-exempt).
    permit_fee = Column(Numeric(10, 2), nullable=True)
    # Optional add-ons the client actually selected on the public proposal and
    # included at signature (line-item ids). Folded into the totals on accept.
    selected_optional_line_ids = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="quotes")
    items = relationship(
        "QuoteLineItem",
        back_populates="quote",
        cascade="all, delete-orphan",
    )
    sub_bids = relationship("SubBid", back_populates="quote", cascade="all, delete-orphan")


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
    # Interactive client upgrade: optional add-ons are excluded from the
    # default grand total and offered on the public proposal as checkboxes;
    # the client's selections are folded into the quote on signature.
    is_optional = Column(Boolean, nullable=False, default=False)
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


class SubBid(Base):
    """A secure public bid-request link for one trade/labor scope on a quote.

    The contractor picks line items, gets an unguessable /sub-bid/<token> URL,
    and a subcontractor submits a lump-sum bid on it. The bid is written back
    onto the master quote as a subcontractor line when submitted."""

    __tablename__ = "sub_bids"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(36), unique=True, index=True, nullable=False)
    status = Column(String, nullable=False, default="open")  # open / submitted
    # The scope lines this sub is bidding on (ids only -- never pricing).
    selected_line_ids = Column(JSON, nullable=True)
    contractor_notes = Column(Text, nullable=True)
    # The subcontractor's submission.
    bid_amount = Column(Numeric(12, 2), nullable=True)
    bid_notes = Column(Text, nullable=True)
    bidder_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)

    quote = relationship("Quote", back_populates="sub_bids")


class MilestoneDraw(Base):
    """A payment-milestone draw request for an in-progress job.

    The contractor requests a draw for an unreleased payment stage, attaches
    up to 3 completion photos + short notes, and the homeowner approves it via
    a secure /milestone/<token> link -- which releases the payment stage."""

    __tablename__ = "milestone_draws"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(36), unique=True, index=True, nullable=False)
    # Snapshot of the stage this draw is for (so approval stays meaningful
    # even if the schedule is later edited).
    milestone_index = Column(Integer, nullable=False)
    milestone_label = Column(String, nullable=False)
    milestone_percent = Column(Numeric(5, 2), nullable=False, default=0)
    status = Column(String, nullable=False, default="requested")  # requested / approved
    notes = Column(Text, nullable=True)
    photos = Column(JSON, nullable=True)  # up to 3 client-resized JPEG data-URIs
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_at = Column(DateTime(timezone=True), nullable=True)

    quote = relationship("Quote")

