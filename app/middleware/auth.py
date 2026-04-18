from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.firebase_admin import verify_token
from app.services.supabase_client import supabase

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """
    Validates Firebase token.
    Returns { user, member, firm } dict.
    Use this as a base dependency in any protected route.
    """
    firebase_uid = verify_token(credentials.credentials)

    user_res = supabase.table("users") \
        .select("*") \
        .eq("firebase_uid", firebase_uid) \
        .execute()
    if not user_res.data:
        raise HTTPException(status_code=401, detail="User not found")
    user = user_res.data[0]

    member_res = supabase.table("firm_members") \
        .select("role, firm_id, status") \
        .eq("user_id", user["id"]) \
        .eq("status", "active") \
        .execute()
    if not member_res.data:
        raise HTTPException(status_code=403, detail="No active firm membership")
    member = member_res.data[0]

    firm_res = supabase.table("firms") \
        .select("id, name") \
        .eq("id", member["firm_id"]) \
        .execute()
    if not firm_res.data:
        raise HTTPException(status_code=403, detail="Firm not found")
    firm = firm_res.data[0]

    return {
        "user": user,
        "member": member,
        "firm": firm,
    }


def require_roles(*roles: str):
    """
    Role gate — use this to protect routes by role.

    Usage:
        @router.get("/some-route")
        async def route(ctx=Depends(require_roles("owner", "admin"))):
            ...
    """
    def dependency(ctx: dict = Depends(get_current_user)) -> dict:
        if ctx["member"]["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required roles: {', '.join(roles)}"
            )
        return ctx
    return dependency


# ─── Prebuilt role dependencies ───────────────────────────────
# Use these directly in routes instead of writing require_roles every time

def owner_only(ctx: dict = Depends(require_roles("owner"))) -> dict:
    return ctx

def admin_and_above(ctx: dict = Depends(require_roles("owner", "admin"))) -> dict:
    return ctx

def all_members(ctx: dict = Depends(get_current_user)) -> dict:
    return ctx