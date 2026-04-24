from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.middleware.auth import all_members, admin_and_above
from app.services.supabase_client import supabase

router = APIRouter()

VALID_ACTIONS = {"pipeline", "rejected", "watchlist"}

SORT_MAP = {
    "score_desc":         ("score",        True),
    "score_asc":          ("score",        False),
    "scored_on_desc":     ("scored_at",    True),
    "scored_on_asc":      ("scored_at",    False),
    "company_name_asc":   ("company_name", False),
    "company_name_desc":  ("company_name", True),
}


class ActionBody(BaseModel):
    action: str


class BulkActionBody(BaseModel):
    deck_ids: list[str]
    action: str


# ─── Scoreboard ────────────────────────────────────────────────

@router.get("/scoreboard")
async def get_scoreboard(
    score_min: Optional[int] = Query(None),
    score_max: Optional[int] = Query(None),
    sector: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    uploaded_by: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: str = Query("score_desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]
    role = ctx["member"]["role"]

    try:
        query = (
            supabase.table("decks")
            .select("*", count="exact")
            .eq("firm_id", firm_id)
            .eq("status", "scored")
            .eq("scoreboard_status", "active")
        )

        if role == "analyst":
            query = query.eq("uploaded_by", user_id)

        if score_min is not None:
            query = query.gte("score", score_min)
        if score_max is not None:
            query = query.lte("score", score_max)
        if sector:
            query = query.eq("sector", sector)
        if stage:
            query = query.eq("stage", stage)
        if uploaded_by:
            query = query.eq("uploaded_by", uploaded_by)
        if date_from:
            query = query.gte("scored_at", date_from)
        if date_to:
            query = query.lte("scored_at", date_to)

        sort_col, sort_desc = SORT_MAP.get(sort_by, ("score", True))
        offset = (page - 1) * limit
        res = query.order(sort_col, desc=sort_desc).range(offset, offset + limit - 1).execute()

        uploader_ids = list({d["uploaded_by"] for d in res.data if d.get("uploaded_by")})
        users_map = {}
        if uploader_ids:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", uploader_ids)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}

        data = [
            {**deck, "uploader_name": users_map.get(deck.get("uploaded_by"), "")}
            for deck in res.data
        ]

        return {"data": data, "total": res.count, "page": page, "limit": limit}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch scoreboard: {str(e)}")


# ─── Single action ─────────────────────────────────────────────

@router.patch("/scoreboard/{deck_id}/action")
async def action_deck(
    deck_id: str,
    body: ActionBody,
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    if body.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of: {', '.join(sorted(VALID_ACTIONS))}")

    try:
        res = (
            supabase.table("decks")
            .select("id, scoreboard_status")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    if res.data[0]["scoreboard_status"] != "active":
        raise HTTPException(status_code=400, detail="Deck is not in active scoreboard status")

    try:
        supabase.table("decks").update({
            "scoreboard_status": body.action,
            "actioned_by": ctx["user"]["id"],
            "actioned_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update deck: {str(e)}")

    return {"deck_id": deck_id, "scoreboard_status": body.action}


# ─── Bulk action ───────────────────────────────────────────────

@router.patch("/scoreboard/bulk-action")
async def bulk_action_decks(
    body: BulkActionBody,
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    if body.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of: {', '.join(sorted(VALID_ACTIONS))}")

    if not body.deck_ids:
        raise HTTPException(status_code=400, detail="deck_ids cannot be empty")

    try:
        res = (
            supabase.table("decks")
            .update({
                "scoreboard_status": body.action,
                "actioned_by": ctx["user"]["id"],
                "actioned_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("firm_id", firm_id)
            .in_("id", body.deck_ids)
            .eq("scoreboard_status", "active")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk action failed: {str(e)}")

    return {"updated": len(res.data), "action": body.action}


# ─── Archived ──────────────────────────────────────────────────

@router.get("/scoreboard/archived")
async def get_archived(
    archive_type: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    if archive_type not in ("rejected", "watchlist"):
        raise HTTPException(status_code=400, detail="archive_type must be rejected or watchlist")

    try:
        offset = (page - 1) * limit
        res = (
            supabase.table("decks")
            .select("*", count="exact")
            .eq("firm_id", firm_id)
            .eq("scoreboard_status", archive_type)
            .order("actioned_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    uploader_ids = list({d["uploaded_by"] for d in res.data if d.get("uploaded_by")})
    users_map = {}
    if uploader_ids:
        try:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", uploader_ids)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    data = [
        {**deck, "uploader_name": users_map.get(deck.get("uploaded_by"), "")}
        for deck in res.data
    ]

    return {"data": data, "total": res.count, "page": page, "limit": limit}
