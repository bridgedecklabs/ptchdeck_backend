from pydantic import BaseModel, EmailStr
from typing import Dict, Optional


# ─── Request Models ───────────────────────────────────────────

class EmailRegisterRequest(BaseModel):
    firebase_uid: str
    email: EmailStr
    full_name: str
    company_name: str


class GoogleAuthRequest(BaseModel):
    firebase_uid: str
    email: EmailStr
    full_name: str


class CompleteProfileRequest(BaseModel):
    firebase_uid: str
    company_name: str


class InviteRequest(BaseModel):
    email: EmailStr
    role: str  # 'admin' or 'user'


class AcceptInviteRequest(BaseModel):
    token: str
    firebase_uid: str
    full_name: str


# ─── Response Models ──────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    full_name: str
    email: str


class FirmOut(BaseModel):
    id: str
    name: str


class AuthResponse(BaseModel):
    user: UserOut
    firm: FirmOut
    role: str
    permissions: Dict[str, bool]


class NeedsCompanyResponse(BaseModel):
    needs_company: bool
    firebase_uid: str
    email: str
    full_name: str


class MessageResponse(BaseModel):
    message: str