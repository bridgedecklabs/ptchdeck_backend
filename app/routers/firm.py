import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Depends
from app.services.supabase_client import supabase
from app.middleware.auth import admin_and_above, owner_only
from app.services.email_service import send_invite_email
from pydantic import BaseModel

INVITE_EXPIRE_HOURS = 48

router = APIRouter()


class UpdateRoleRequest(BaseModel):
    role: str  # 'admin' or 'user'


class UpdatePermissionRequest(BaseModel):
    module: str
    enabled: bool


# ─── Get all firm members ──────────────────────────────────────

@router.get("/firm/members")
async def get_members(ctx=Depends(admin_and_above)):
    """
    Returns all members of the caller's firm.
    Owner and Admin only.
    """
    try:
        firm_id = ctx["firm"]["id"]

        members_res = supabase.table("firm_members") \
            .select("id, role, status, joined_at, user_id, invited_by") \
            .eq("firm_id", firm_id) \
            .execute()

        if not members_res.data:
            return {"members": []}

        # Fetch user details for each member
        user_ids = [m["user_id"] for m in members_res.data if m["user_id"]]
        users_res = supabase.table("users") \
            .select("id, full_name, email") \
            .in_("id", user_ids) \
            .execute()

        users_map = {u["id"]: u for u in users_res.data}

        members = []
        for m in members_res.data:
            user = users_map.get(m["user_id"], {})
            members.append({
                "member_id": m["id"],
                "user_id": m["user_id"],
                "full_name": user.get("full_name", ""),
                "email": user.get("email", ""),
                "role": m["role"],
                "status": m["status"],
                "joined_at": m["joined_at"],
            })

        return {"members": members}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch members: {str(e)}")


# ─── Update member role ────────────────────────────────────────

@router.patch("/firm/members/{member_id}/role")
async def update_member_role(
    member_id: str,
    body: UpdateRoleRequest,
    ctx=Depends(owner_only),
):
    """
    Change a member's role.
    Owner only.
    Cannot change owner role.
    """
    try:
        firm_id = ctx["firm"]["id"]

        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role must be admin or user")

        # Fetch the target member
        target_res = supabase.table("firm_members") \
            .select("*") \
            .eq("id", member_id) \
            .eq("firm_id", firm_id) \
            .execute()

        if not target_res.data:
            raise HTTPException(status_code=404, detail="Member not found")
        target = target_res.data[0]

        # Cannot change owner role
        if target["role"] == "owner":
            raise HTTPException(status_code=403, detail="Cannot change owner role")

        # Cannot change your own role
        if target["user_id"] == ctx["user"]["id"]:
            raise HTTPException(status_code=403, detail="Cannot change your own role")

        supabase.table("firm_members") \
            .update({"role": body.role}) \
            .eq("id", member_id) \
            .execute()

        return {"message": "Role updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update role: {str(e)}")


# ─── Remove member ─────────────────────────────────────────────

@router.delete("/firm/members/{member_id}")
async def remove_member(
    member_id: str,
    ctx=Depends(admin_and_above),
):
    """
    Remove a member from the firm.
    Owner and Admin can remove.
    Cannot remove owner.
    Cannot remove yourself.
    """
    try:
        firm_id = ctx["firm"]["id"]

        target_res = supabase.table("firm_members") \
            .select("*") \
            .eq("id", member_id) \
            .eq("firm_id", firm_id) \
            .execute()

        if not target_res.data:
            raise HTTPException(status_code=404, detail="Member not found")
        target = target_res.data[0]

        # Cannot remove owner
        if target["role"] == "owner":
            raise HTTPException(status_code=403, detail="Cannot remove the owner")

        # Cannot remove yourself
        if target["user_id"] == ctx["user"]["id"]:
            raise HTTPException(status_code=403, detail="Cannot remove yourself")

        # Admin cannot remove another admin
        if ctx["member"]["role"] == "admin" and target["role"] == "admin":
            raise HTTPException(status_code=403, detail="Admin cannot remove another admin")

        supabase.table("firm_members") \
            .delete() \
            .eq("id", member_id) \
            .execute()

        return {"message": "Member removed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove member: {str(e)}")


# ─── Resend invite ─────────────────────────────────────────────

@router.post("/firm/members/invite/{member_id}/resend")
async def resend_invite(
    member_id: str,
    ctx=Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("firm_members")
            .select("id, invite_email")
            .eq("id", member_id)
            .eq("firm_id", firm_id)
            .eq("status", "pending")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Pending invite not found")

    invite_email = res.data[0]["invite_email"]
    new_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRE_HOURS)

    try:
        supabase.table("firm_members").update({
            "invite_token": new_token,
            "invite_expires_at": expires_at.isoformat(),
        }).eq("id", member_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update invite: {str(e)}")

    try:
        send_invite_email(invite_email, new_token, ctx["firm"]["name"])
    except Exception as e:
        print(f"Resend invite email failed (token updated): {e}")

    return {"success": True, "message": "Invite resent"}


# ─── Cancel invite ─────────────────────────────────────────────

@router.delete("/firm/members/invite/{member_id}")
async def cancel_invite(
    member_id: str,
    ctx=Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("firm_members")
            .select("id")
            .eq("id", member_id)
            .eq("firm_id", firm_id)
            .eq("status", "pending")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Pending invite not found")

    try:
        supabase.table("firm_members").update({
            "status": "cancelled",
            "invite_token": None,
            "invite_expires_at": None,
        }).eq("id", member_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel invite: {str(e)}")

    return {"success": True, "message": "Invite cancelled"}


# ─── Get firm permissions ──────────────────────────────────────

VALID_MODULES = [
    "dashboard", "upload_queue", "scoreboard", "pipeline",
    "portfolio", "cohort_builder", "connectors",
    "manage_access", "billing", "settings",
]

@router.get("/firm/permissions")
async def get_permissions(ctx=Depends(admin_and_above)):
    """
    Returns module permissions for the firm as a flat object.
    Owner and Admin only.
    """
    try:
        firm_id = ctx["firm"]["id"]

        res = supabase.table("firm_permissions") \
            .select(", ".join(VALID_MODULES)) \
            .eq("firm_id", firm_id) \
            .execute()

        if not res.data:
            raise HTTPException(status_code=404, detail="Permissions not found for this firm")

        return res.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch permissions: {str(e)}")


# ─── Update firm permission ────────────────────────────────────

@router.patch("/firm/permissions")
async def update_permission(
    body: UpdatePermissionRequest,
    ctx=Depends(admin_and_above),
):
    """
    Toggle a single module on or off.
    Owner and Admin only.
    """
    try:
        firm_id = ctx["firm"]["id"]

        if body.module not in VALID_MODULES:
            raise HTTPException(status_code=400, detail="Invalid module name")

        supabase.table("firm_permissions") \
            .update({body.module: body.enabled}) \
            .eq("firm_id", firm_id) \
            .execute()

        return {"module": body.module, "enabled": body.enabled}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update permission: {str(e)}")