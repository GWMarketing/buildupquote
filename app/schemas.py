"""Pydantic schemas for user/organization input & output.

UserCreate is what a register endpoint will accept (optionally naming the
organization to create on first sign-up); UserOut / OrganizationOut are
what the API returns -- they never include a hashed password.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class OrganizationCreate(BaseModel):
    name: str


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    license_number: Optional[str] = None
    tax_id: Optional[str] = None
    default_payment_terms: Optional[str] = None
    currency_symbol: Optional[str] = None
    # Master contract / terms of service for quoted work.
    master_contract_text: Optional[str] = None
    # The representative attached to this business.
    full_name: Optional[str] = None
    job_title: Optional[str] = None


class OrganizationOut(BaseModel):
    id: int
    name: str
    slug: str
    bio: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    license_number: Optional[str] = None
    tax_id: Optional[str] = None
    default_payment_terms: Optional[str] = None
    currency_symbol: Optional[str] = None
    logo_url: Optional[str] = None
    master_contract_text: Optional[str] = None
    master_contract_pdf_url: Optional[str] = None
    # Stripe subscription state (server-managed via the billing webhook).
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    subscription_tier: str = "starter"  # starter / pro / enterprise
    subscription_status: str = "trialing"  # trialing / active / past_due / canceled
    trial_ends_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationProfileOut(OrganizationOut):
    """What GET /api/organization/me returns: the full business profile plus
    the representative's name and job title."""

    full_name: Optional[str] = None
    job_title: Optional[str] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    organization_name: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool
    role: str
    is_admin: bool = False  # platform admin (full-instance access)
    organization_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserProfileOut(BaseModel):
    """The authenticated user's own profile (GET /api/users/me)."""

    id: int
    email: EmailStr
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    role: str
    is_admin: bool = False
    organization_id: Optional[int] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Parametric assemblies (Phase 5 -- advanced quoting engine)
# ---------------------------------------------------------------------------

class AssemblyComponentOut(BaseModel):
    description: str
    item_type: str
    unit: str
    formula: str
    default_unit_cost: float
    default_markup_percent: float

    model_config = {"from_attributes": True}


class AssemblyOut(BaseModel):
    id: int
    code: str
    name: str
    category: str
    description: Optional[str] = None
    required_inputs: list[str] = []
    calculator: Optional[str] = None
    created_at: datetime
    components: list[AssemblyComponentOut] = []

    model_config = {"from_attributes": True}


class AssemblyCalculateRequest(BaseModel):
    dimensions: dict[str, float]


class ApplyAssemblyRequest(BaseModel):
    code: str
    dimensions: dict[str, float]


class AssemblyBuildRequest(BaseModel):
    """POST /api/catalog/calculate-assembly -- run a hand-written assembly
    calculator by type ('stud_wall' or 'floor_tiling') for a set of
    dimensions."""

    assembly_type: str
    length: float
    width: float = 0.0
    height: float = 0.0


class CatalogItemCreate(BaseModel):
    """POST /api/catalog/items -- a new entry in the standard rate catalog."""

    trade: str
    canonical_name: str
    unit: str = "unit"
    default_unit_cost: float = 0.0
    default_trade_type: str = "Material"


class CatalogItemOut(BaseModel):
    """A rate-catalog item as the catalog manager page shows it. The stored
    default_trade_type is a display label ("Material", "Labor",
    "Subcontractor", "Plant"); the autocorrect endpoint lowercases it for the
    quote builder's item_type contract."""

    id: int
    trade: str
    canonical_name: str
    unit: str
    default_unit_cost: float
    default_trade_type: str

    model_config = {"from_attributes": True}


class AssemblyLineOut(BaseModel):
    description: str
    item_type: str
    trade: Optional[str] = None
    quantity: float
    unit: str
    unit_cost: float
    markup_percent: float
    subtotal: float

    model_config = {"from_attributes": True}


class AssemblyCalculateResponse(BaseModel):
    assembly_code: str
    assembly_name: str
    lines: list[AssemblyLineOut]
    total: float


# ---------------------------------------------------------------------------
# Clients & quotes (CRM frontend)
# ---------------------------------------------------------------------------

class ClientCreate(BaseModel):
    name: str
    site_address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class ClientOut(BaseModel):
    id: int
    organization_id: int
    name: str
    site_address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QuickParseRequest(BaseModel):
    """Free-form pasted lead text (one contact per blank line)."""

    text: str


class LeadParseRequest(BaseModel):
    """A single free-form lead (Name/email/phone/site in loose text)."""

    raw_text: str


class ClientImportResult(BaseModel):
    created: int
    skipped: int
    clients: list[ClientOut]


class PublicQuoteAcceptRequest(BaseModel):
    """POST /api/public/quotes/{public_uuid}/accept -- the client's digital
    sign-off. signature_data is the canvas PNG (data-URI or raw base64);
    client_name and signer_email are captured for the audit trail."""

    signature_data: str
    client_name: str = ""
    signer_email: str = ""


class QuoteCreate(BaseModel):
    title: str = "Untitled quote"
    client_id: Optional[int] = None


class QuoteUpdate(BaseModel):
    title: Optional[str] = None
    site_address: Optional[str] = None
    status: Optional[str] = None  # draft / sent / accepted
    client_id: Optional[int] = None
    tax_rate_percent: Optional[float] = None  # flat rate persisted for the PDF
    # Master contract attachment for this quote.
    include_contract: Optional[bool] = None
    custom_contract_override: Optional[str] = None


class SendQuoteEmailRequest(BaseModel):
    """POST /api/quotes/{id}/send-email -- optional personal message that
    rides along in the client-facing proposal email."""

    message: str = ""


class QuoteLineWrite(BaseModel):
    description: str
    item_type: str = "material"  # material / labor / plant / subcontractor
    trade: Optional[str] = None  # auto-tagged from the trade lexicon
    quantity: float = 0
    unit: str = "item"
    unit_cost: float = 0
    markup_percent: float = 20.0


class QuoteLineOut(BaseModel):
    id: int
    description: str
    item_type: str
    trade: Optional[str] = None
    quantity: float
    unit: str
    unit_cost: float
    markup_percent: float
    line_total: float
    position: int

    model_config = {"from_attributes": True}


class QuoteOut(BaseModel):
    id: int
    organization_id: Optional[int]
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    public_uuid: Optional[str] = None  # unguessable shareable link key
    title: str
    site_address: Optional[str] = None
    status: str
    subtotal: float
    tax_amount: float = 0
    tax_rate_percent: Optional[float] = None
    total: float
    include_contract: bool = True
    custom_contract_override: Optional[str] = None
    created_at: datetime
    line_count: int = 0


class QuoteDetailOut(QuoteOut):
    lines: list[QuoteLineOut] = []


class ParseToQuoteRequest(BaseModel):
    """Rows + claim context from the insurance parser, turned into a draft
    Quote by POST /api/quotes/from-parse."""

    rows: list[dict] = []
    claim_fields: dict = {}
    client_id: Optional[int] = None


class GoogleAuthRequest(BaseModel):
    """ID token from the Google Identity Services button (response.credential)."""

    credential: str


class RegisterResponse(BaseModel):
    """What /api/auth/register returns: the new user plus a fresh JWT so
    the page can sign in immediately (no second login round-trip)."""
    user: UserOut
    access_token: str
    token_type: str = "bearer"


class LexiconMatchRequest(BaseModel):
    description: str

