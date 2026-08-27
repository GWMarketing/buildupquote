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


class OrganizationOut(BaseModel):
    id: int
    name: str
    slug: str
    created_at: datetime

    model_config = {"from_attributes": True}


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
    organization_id: Optional[int] = None
    created_at: datetime

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
    created_at: datetime
    components: list[AssemblyComponentOut] = []

    model_config = {"from_attributes": True}


class AssemblyCalculateRequest(BaseModel):
    dimensions: dict[str, float]


class ApplyAssemblyRequest(BaseModel):
    code: str
    dimensions: dict[str, float]


class AssemblyLineOut(BaseModel):
    description: str
    item_type: str
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

