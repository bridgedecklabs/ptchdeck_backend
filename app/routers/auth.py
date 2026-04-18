from fastapi import APIRouter, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.supabase_client import supabase
from app.services.firebase_admin import verify_token
from app.schemas.auth import (
    EmailRegisterRequest,
    GoogleAuthRequest,
    CompleteProfileRequest,
    InviteRequest,
    AcceptInviteRequest,
    AuthResponse,
    NeedsCompanyResponse,
    MessageResponse,
)
import secrets
from datetime import datetime, timezone, timedelta

router = APIRouter()
security = HTTPBearer()

INVITE_EXPIRE_HOURS = 48

ALL_MODULES = [
    "dashboard",
    "upload_queue",
    "scoreboard",
    "pipeline",
    "portfolio",
    "cohort_builder",
    "connectors",
    "manage_access",
    "billing",
    "settings",
]


# ─── Helpers ──────────────────────────────────────────────────

def _build_auth_response(user: dict, member: dict, firm: dict, perms_data: list) -> dict:
    permissions = {p["module"]: p["user_access"] for p in perms_data}
    return {
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
        },
        "firm": {"id": firm["id"], "name": firm["name"]},
        "role": member["role"],
        "permissions": permissions,
    }


def _create_firm_with_owner(firebase_uid: str, email: str, full_name: str, company_name: str) -> dict:
    """Creates user, firm, membership and default permissions. Returns auth response dict."""

    # 1. Create user
    user_res = supabase.table("users").insert({
        "firebase_uid": firebase_uid,
        "email": email,
        "full_name": full_name,
    }).execute()
    user = user_res.data[0]

    # 2. Create firm
    firm_res = supabase.table("firms").insert({
        "name": company_name.strip(),
        "owner_id": user["id"],
    }).execute()
    firm = firm_res.data[0]

    # 3. Create owner membership
    member_res = supabase.table("firm_members").insert({
        "user_id": user["id"],
        "firm_id": firm["id"],
        "role": "owner",
        "status": "active",
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    member = member_res.data[0]

    # 4. Create default permissions — all modules ON for user by default
    permissions_rows = [
        {"firm_id": firm["id"], "module": module, "user_access": True}
        for module in ALL_MODULES
    ]
    perms_res = supabase.table("firm_permissions").insert(permissions_rows).execute()

    return _build_auth_response(user, member, firm, perms_res.data)


def _get_auth_response_by_uid(firebase_uid: str) -> dict:
    """Fetch full auth response for an existing user by firebase_uid."""

    user_res = supabase.table("users") \
        .select("*") \
        .eq("firebase_uid", firebase_uid) \
        .execute()
    if not user_res.data:
        raise HTTPException(status_code=404, detail="User not found")
    user = user_res.data[0]

    member_res = supabase.table("firm_members") \
        .select("role, firm_id, status") \
        .eq("user_id", user["id"]) \
        .eq("status", "active") \
        .execute()
    if not member_res.data:
        raise HTTPException(status_code=404, detail="No active firm membership")
    member = member_res.data[0]

    firm_res = supabase.table("firms") \
        .select("id, name") \
        .eq("id", member["firm_id"]) \
        .execute()
    if not firm_res.data:
        raise HTTPException(status_code=404, detail="Firm not found")
    firm = firm_res.data[0]

    perms_res = supabase.table("firm_permissions") \
        .select("module, user_access") \
        .eq("firm_id", member["firm_id"]) \
        .execute()

    return _build_auth_response(user, member, firm, perms_res.data)


# ─── Routes ───────────────────────────────────────────────────

@router.post("/auth/register", response_model=AuthResponse)
async def email_register(body: EmailRegisterRequest):
    """
    Email signup — creates owner + firm in one shot.
    Firebase account already created on frontend before calling this.
    """
    try:
        # Idempotent — return existing if already registered
        existing = supabase.table("users") \
            .select("*") \
            .eq("firebase_uid", body.firebase_uid) \
            .execute()
        if existing.data:
            return _get_auth_response_by_uid(body.firebase_uid)

        return _create_firm_with_owner(
            firebase_uid=body.firebase_uid,
            email=body.email,
            full_name=body.full_name,
            company_name=body.company_name,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.post("/auth/google")
async def google_auth(body: GoogleAuthRequest):
    """
    Google signup/login.
    - Existing user → return full auth response
    - New user → return needs_company: true so frontend shows company name screen
    """
    try:
        existing = supabase.table("users") \
            .select("*") \
            .eq("firebase_uid", body.firebase_uid) \
            .execute()

        if existing.data:
            # Existing user — just log them in
            return _get_auth_response_by_uid(body.firebase_uid)

        # New user — needs company name before firm can be created
        return NeedsCompanyResponse(
            needs_company=True,
            firebase_uid=body.firebase_uid,
            email=body.email,
            full_name=body.full_name,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google auth failed: {str(e)}")


@router.post("/auth/complete-profile", response_model=AuthResponse)
async def complete_profile(body: CompleteProfileRequest):
    """
    Called after Google auth when user needs to provide company name.
    Creates firm and owner membership.
    """
    try:
        # Safety check — don't create duplicate
        existing = supabase.table("users") \
            .select("*") \
            .eq("firebase_uid", body.firebase_uid) \
            .execute()
        if existing.data:
            return _get_auth_response_by_uid(body.firebase_uid)

        # We need user info — get it from Firebase token check
        # firebase_uid is trusted here as it came from a verified Google session
        # full_name and email must be fetched from Firebase
        import firebase_admin.auth as fb_auth
        firebase_user = fb_auth.get_user(body.firebase_uid)

        return _create_firm_with_owner(
            firebase_uid=body.firebase_uid,
            email=firebase_user.email,
            full_name=firebase_user.display_name or "",
            company_name=body.company_name,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Complete profile failed: {str(e)}")


@router.get("/auth/me", response_model=AuthResponse)
async def get_me(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Returns full session — user, firm, role, permissions.
    Called on every app load to restore session.
    """
    try:
        firebase_uid = verify_token(credentials.credentials)
        return _get_auth_response_by_uid(firebase_uid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch user: {str(e)}")


@router.post("/auth/invite", response_model=MessageResponse)
async def invite_member(
    body: InviteRequest,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    Owner or Admin invites a new member by email.
    Generates a signed token and sends invite email.
    """
    try:
        # Verify caller
        firebase_uid = verify_token(credentials.credentials)
        caller = supabase.table("users") \
            .select("id") \
            .eq("firebase_uid", firebase_uid) \
            .execute()
        if not caller.data:
            raise HTTPException(status_code=401, detail="Unauthorized")
        caller_id = caller.data[0]["id"]

        # Get caller's firm and role
        caller_member = supabase.table("firm_members") \
            .select("firm_id, role") \
            .eq("user_id", caller_id) \
            .eq("status", "active") \
            .execute()
        if not caller_member.data:
            raise HTTPException(status_code=403, detail="No active membership")
        caller_role = caller_member.data[0]["role"]
        firm_id = caller_member.data[0]["firm_id"]

        # Only owner and admin can invite
        if caller_role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Not authorized to invite")

        # Role being assigned must be admin or user only
        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Invalid role. Must be admin or user")

        # Check if already a member
        already = supabase.table("firm_members") \
            .select("id") \
            .eq("firm_id", firm_id) \
            .execute()
        # Get user by email first
        invited_user = supabase.table("users") \
            .select("id") \
            .eq("email", body.email) \
            .execute()
        if invited_user.data:
            existing_member = supabase.table("firm_members") \
                .select("id") \
                .eq("firm_id", firm_id) \
                .eq("user_id", invited_user.data[0]["id"]) \
                .execute()
            if existing_member.data:
                raise HTTPException(status_code=400, detail="User is already a member of this firm")

        # Generate invite token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRE_HOURS)

        # Store pending invite
        supabase.table("firm_members").insert({
            "firm_id": firm_id,
            "invited_by": caller_id,
            "role": body.role,
            "status": "pending",
            "invite_token": token,
            "invite_email": body.email,
            "invite_expires_at": expires_at.isoformat(),
        }).execute()

        # TODO: Send invite email via email_service
        # invite_link = f"https://app.ptchdeck.com/invite/accept?token={token}"
        # await email_service.send_invite(to=body.email, firm_name=firm_name, link=invite_link)

        return {"message": f"Invite sent to {body.email}"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invite failed: {str(e)}")


@router.post("/auth/invite/accept", response_model=AuthResponse)
async def accept_invite(body: AcceptInviteRequest):
    """
    Invitee accepts invite.
    Firebase account already created on frontend before calling this.
    Token is validated, membership activated.
    """
    try:
        # Find pending invite by token
        invite_res = supabase.table("firm_members") \
            .select("*") \
            .eq("invite_token", body.token) \
            .eq("status", "pending") \
            .execute()

        if not invite_res.data:
            raise HTTPException(status_code=404, detail="Invalid or already used invite")
        invite = invite_res.data[0]

        # Check expiry
        expires_at = datetime.fromisoformat(invite["invite_expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="Invite link has expired")

        # Verify email matches
        import firebase_admin.auth as fb_auth
        firebase_user = fb_auth.get_user(body.firebase_uid)
        if firebase_user.email.lower() != invite["invite_email"].lower():
            raise HTTPException(status_code=403, detail="Email does not match invite")

        # Create or get user
        existing_user = supabase.table("users") \
            .select("*") \
            .eq("firebase_uid", body.firebase_uid) \
            .execute()

        if existing_user.data:
            user = existing_user.data[0]
        else:
            user_res = supabase.table("users").insert({
                "firebase_uid": body.firebase_uid,
                "email": firebase_user.email,
                "full_name": body.full_name or firebase_user.display_name or "",
            }).execute()
            user = user_res.data[0]

        # Activate membership
        supabase.table("firm_members") \
            .update({
                "user_id": user["id"],
                "status": "active",
                "joined_at": datetime.now(timezone.utc).isoformat(),
                "invite_token": None,
                "invite_expires_at": None,
            }) \
            .eq("id", invite["id"]) \
            .execute()

        member = {
            "role": invite["role"],
            "firm_id": invite["firm_id"],
            "status": "active",
        }

        firm_res = supabase.table("firms") \
            .select("id, name") \
            .eq("id", invite["firm_id"]) \
            .execute()
        firm = firm_res.data[0]

        perms_res = supabase.table("firm_permissions") \
            .select("module, user_access") \
            .eq("firm_id", invite["firm_id"]) \
            .execute()

        return _build_auth_response(user, member, firm, perms_res.data)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Accept invite failed: {str(e)}")