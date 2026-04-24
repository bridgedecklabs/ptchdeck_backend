from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.middleware.auth import all_members, admin_and_above
from app.services.supabase_client import supabase

router = APIRouter()

ALLOWED_TOOLS = {
    "gmail", "outlook", "slack", "teams", "google_drive",
    "whatsapp_business", "telegram", "dropbox", "notion", "zoho_cliq",
}

SENSITIVE_FIELDS = {"oauth_token", "oauth_refresh", "token_expires_at"}


def _strip_tokens(connector: dict) -> dict:
    return {k: v for k, v in connector.items() if k not in SENSITIVE_FIELDS}


class ConnectBody(BaseModel):
    tool: str
    config: Optional[dict] = None


# ─── List connectors ───────────────────────────────────────────

@router.get("/connectors")
async def list_connectors(ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("connectors")
            .select("*")
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    connector_ids_by_user = list({c["connected_by"] for c in res.data if c.get("connected_by")})
    users_map = {}
    if connector_ids_by_user:
        try:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", connector_ids_by_user)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception:
            pass

    connectors = [
        {**_strip_tokens(c), "connected_by_name": users_map.get(c.get("connected_by"), "")}
        for c in res.data
    ]

    return {"connectors": connectors}


# ─── Connect ───────────────────────────────────────────────────

@router.post("/connectors/connect")
async def connect_tool(body: ConnectBody, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    if body.tool not in ALLOWED_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tool. Allowed: {', '.join(sorted(ALLOWED_TOOLS))}",
        )

    try:
        existing = (
            supabase.table("connectors")
            .select("id")
            .eq("firm_id", firm_id)
            .eq("tool", body.tool)
            .execute()
        )
        if existing.data:
            raise HTTPException(status_code=400, detail=f"{body.tool} is already connected for this firm")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        res = supabase.table("connectors").insert({
            "firm_id": firm_id,
            "tool": body.tool,
            "config": body.config or {},
            "status": "connected",
            "connected_by": ctx["user"]["id"],
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect tool: {str(e)}")

    return _strip_tokens(res.data[0])


# ─── Disconnect ────────────────────────────────────────────────

@router.delete("/connectors/{connector_id}")
async def disconnect_tool(connector_id: str, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("connectors")
            .select("id")
            .eq("id", connector_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Connector not found")

    try:
        supabase.table("connectors").update({
            "status": "disconnected",
            "oauth_token": None,
            "oauth_refresh": None,
            "token_expires_at": None,
        }).eq("id", connector_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {str(e)}")

    return {"success": True}


# ─── List imports ──────────────────────────────────────────────

@router.get("/connectors/{connector_id}/imports")
async def list_imports(
    connector_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        connector_res = (
            supabase.table("connectors")
            .select("id")
            .eq("id", connector_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not connector_res.data:
        raise HTTPException(status_code=404, detail="Connector not found")

    try:
        offset = (page - 1) * limit
        res = (
            supabase.table("connector_imports")
            .select("*", count="exact")
            .eq("connector_id", connector_id)
            .order("imported_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"data": res.data, "total": res.count, "page": page, "limit": limit}


# ─── Reconnect ─────────────────────────────────────────────────

@router.post("/connectors/{connector_id}/reconnect")
async def reconnect_tool(connector_id: str, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("connectors")
            .select("id")
            .eq("id", connector_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Connector not found")

    try:
        supabase.table("connectors").update({
            "status": "connected",
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", connector_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reconnect: {str(e)}")

    return {"connector_id": connector_id, "status": "connected"}
