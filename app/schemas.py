"""Pydantic schemas for user input/output (CRM groundwork).

UserCreate is what a register endpoint will accept; UserOut is what the
API returns -- note it never includes the hashed password.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
