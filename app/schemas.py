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

