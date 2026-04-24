import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import all_members, admin_and_above, owner_only
from app.services.supabase_client import supabase

router = APIRouter()

VALID_EVENT_TYPES = {
    "deck_scored",
    "deck_failed",
    "deck_imported_connector",
    "cohort_submission",
    "cohort_deadline",
    "duplicate_blocked",
    "member_joined",
    "invite_accepted",
}


def _make_inbound_slug(firm_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", firm_name.lower()).strip("-")


class FirmUpdateBody(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None
    website: Optional[str] = None
    sector_focus: Optional[str] = None
    stage_focus: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


class NotificationUpdateBody(BaseModel):
    enabled: bool


class ProfileUpdateBody(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class TransferOwnershipBody(BaseModel):
    new_owner_id: str
    firm_name_confirmation: str


class DeleteWorkspaceBody(BaseModel):
    firm_name_confirmation: str


# ─── Firm settings ─────────────────────────────────────────────

@router.get("/settings/firm")
async def get_firm_settings(ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("firms")
            .select("id, name, logo_url, website, sector_focus, stage_focus, location, description, inbound_email, created_at")
            .eq("id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Firm not found")

    return res.data[0]


@router.patch("/settings/firm")
async def update_firm_settings(body: FirmUpdateBody, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        res = supabase.table("firms").update(updates).eq("id", firm_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update firm: {str(e)}")

    return res.data[0]


# ─── Inbound email ─────────────────────────────────────────────

@router.get("/settings/inbound-email")
async def get_inbound_email(ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("firms")
            .select("inbound_email, name")
            .eq("id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Firm not found")

    inbound_email = res.data[0]["inbound_email"]

    if not inbound_email:
        slug = _make_inbound_slug(res.data[0]["name"])
        inbound_email = f"{slug}@in.ptchdeck.com"
        try:
            supabase.table("firms").update({"inbound_email": inbound_email}).eq("id", firm_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save inbound email: {str(e)}")

    return {"inbound_email": inbound_email}


@router.post("/settings/inbound-email/regenerate")
async def regenerate_inbound_email(ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("firms")
            .select("name")
            .eq("id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Firm not found")

    slug = _make_inbound_slug(res.data[0]["name"])
    suffix = secrets.token_hex(2)  # 4 hex chars
    inbound_email = f"{slug}-{suffix}@in.ptchdeck.com"

    try:
        supabase.table("firms").update({"inbound_email": inbound_email}).eq("id", firm_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update inbound email: {str(e)}")

    return {"inbound_email": inbound_email}


# ─── Notification preferences ──────────────────────────────────

@router.get("/settings/notifications")
async def get_notifications(ctx: dict = Depends(all_members)):
    user_id = ctx["user"]["id"]
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("notification_preferences")
            .select("event_type, enabled")
            .eq("user_id", user_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        defaults = [
            {"user_id": user_id, "firm_id": firm_id, "event_type": et, "enabled": True}
            for et in sorted(VALID_EVENT_TYPES)
        ]
        try:
            seeded = supabase.table("notification_preferences").insert(defaults).execute()
            return [{"event_type": r["event_type"], "enabled": r["enabled"]} for r in seeded.data]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to seed notification preferences: {str(e)}")

    return [{"event_type": r["event_type"], "enabled": r["enabled"]} for r in res.data]


@router.patch("/settings/notifications/{event_type}")
async def update_notification(
    event_type: str,
    body: NotificationUpdateBody,
    ctx: dict = Depends(all_members),
):
    if event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid event_type. Valid: {', '.join(sorted(VALID_EVENT_TYPES))}")

    user_id = ctx["user"]["id"]
    firm_id = ctx["firm"]["id"]

    try:
        supabase.table("notification_preferences").upsert({
            "user_id": user_id,
            "firm_id": firm_id,
            "event_type": event_type,
            "enabled": body.enabled,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update preference: {str(e)}")

    return {"event_type": event_type, "enabled": body.enabled}


# ─── User profile ──────────────────────────────────────────────

@router.get("/settings/me")
async def get_profile(ctx: dict = Depends(all_members)):
    user_id = ctx["user"]["id"]

    try:
        res = (
            supabase.table("users")
            .select("id, full_name, email, avatar_url, created_at")
            .eq("id", user_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")

    return res.data[0]


@router.patch("/settings/me")
async def update_profile(body: ProfileUpdateBody, ctx: dict = Depends(all_members)):
    user_id = ctx["user"]["id"]

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        res = supabase.table("users").update(updates).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")

    return res.data[0]


# ─── Transfer ownership ────────────────────────────────────────

@router.post("/settings/transfer-ownership")
async def transfer_ownership(body: TransferOwnershipBody, ctx: dict = Depends(owner_only)):
    firm_id = ctx["firm"]["id"]
    firm_name = ctx["firm"]["name"]
    current_owner_id = ctx["user"]["id"]

    if body.firm_name_confirmation != firm_name:
        raise HTTPException(status_code=400, detail="Firm name confirmation does not match")

    try:
        target_res = (
            supabase.table("firm_members")
            .select("user_id, role")
            .eq("firm_id", firm_id)
            .eq("user_id", body.new_owner_id)
            .eq("status", "active")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not target_res.data:
        raise HTTPException(status_code=404, detail="Member not found in this firm")

    if target_res.data[0]["role"] != "admin":
        raise HTTPException(status_code=400, detail="New owner must be an Admin of this firm")

    try:
        supabase.table("firm_members") \
            .update({"role": "owner"}) \
            .eq("firm_id", firm_id) \
            .eq("user_id", body.new_owner_id) \
            .execute()

        supabase.table("firm_members") \
            .update({"role": "admin"}) \
            .eq("firm_id", firm_id) \
            .eq("user_id", current_owner_id) \
            .execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to transfer ownership: {str(e)}")

    return {"success": True, "message": "Ownership transferred"}


# ─── Delete workspace ──────────────────────────────────────────

@router.delete("/settings/workspace")
async def delete_workspace(body: DeleteWorkspaceBody, ctx: dict = Depends(owner_only)):
    firm_id = ctx["firm"]["id"]
    firm_name = ctx["firm"]["name"]

    if body.firm_name_confirmation != firm_name:
        raise HTTPException(status_code=400, detail="Firm name confirmation does not match")

    deletion_order = [
        ("notification_preferences", "firm_id"),
        ("cohort_submissions",       "firm_id"),
        ("cohorts",                  "firm_id"),
        ("kanban_cards",             "firm_id"),
        ("pipeline_columns",         "firm_id"),
        ("portfolio_companies",      "firm_id"),
        ("decks",                    "firm_id"),
        ("firm_members",             "firm_id"),
        ("firm_permissions",         "firm_id"),
        ("firms",                    "id"),
    ]

    try:
        for table, column in deletion_order:
            supabase.table(table).delete().eq(column, firm_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workspace deletion failed at {table}: {str(e)}")

    return {"success": True}
