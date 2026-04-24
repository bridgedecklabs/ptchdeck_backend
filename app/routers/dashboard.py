import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.middleware.auth import all_members, admin_and_above
from app.services.supabase_client import supabase

router = APIRouter()


class RestoreBody(BaseModel):
    restore_to: str  # "scoreboard" | "pipeline"


class WatchlistActionBody(BaseModel):
    action: str  # "pipeline" | "rejected"


def _get_inbox_column_id(firm_id: str) -> Optional[str]:
    res = (
        supabase.table("pipeline_columns")
        .select("id")
        .eq("firm_id", firm_id)
        .eq("is_fixed", True)
        .limit(1)
        .execute()
    )
    return res.data[0]["id"] if res.data else None


def _create_kanban_card(deck_id: str, firm_id: str, column_id: str, user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("kanban_cards").insert({
        "id": str(uuid.uuid4()),
        "deck_id": deck_id,
        "firm_id": firm_id,
        "column_id": column_id,
        "position": 0,
        "moved_by": user_id,
        "moved_at": now,
        "entered_column_at": now,
        "final_status": "active",
    }).execute()


# ─── Stats ─────────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def get_stats(ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]
    analyst = ctx["member"]["role"] == "analyst"

    today = date.today()
    first_of_month = date(today.year, today.month, 1).isoformat()

    def dq():
        q = supabase.table("decks").select("id", count="exact").eq("firm_id", firm_id)
        if analyst:
            q = q.eq("uploaded_by", user_id)
        return q

    try:
        r_month    = dq().gte("created_at", first_of_month).execute()
        r_proc     = dq().in_("status", ["queued", "processing"]).execute()
        r_scored   = dq().eq("status", "scored").eq("scoreboard_status", "active").execute()
        r_reviewed = dq().in_("scoreboard_status", ["pipeline", "rejected", "watchlist"]).gte("actioned_at", first_of_month).execute()
        r_watch    = dq().eq("scoreboard_status", "watchlist").execute()
        r_rejected = dq().eq("scoreboard_status", "rejected").execute()
        r_pipeline = supabase.table("kanban_cards").select("id", count="exact").eq("firm_id", firm_id).eq("final_status", "active").execute()
        r_invested = supabase.table("kanban_cards").select("id", count="exact").eq("firm_id", firm_id).eq("final_status", "invested").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch stats: {str(e)}")

    return {
        "decks_this_month":    r_month.count    or 0,
        "processing":          r_proc.count     or 0,
        "scored":              r_scored.count   or 0,
        "active_pipeline":     r_pipeline.count or 0,
        "deals_reviewed_month": r_reviewed.count or 0,
        "invested_all_time":   r_invested.count or 0,
        "watchlist_total":     r_watch.count    or 0,
        "rejected_all_time":   r_rejected.count or 0,
    }


# ─── Feed ──────────────────────────────────────────────────────

@router.get("/dashboard/feed")
async def get_feed(ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]
    analyst = ctx["member"]["role"] == "analyst"

    try:
        sq = (
            supabase.table("decks")
            .select("id, company_name, score, scored_at")
            .eq("firm_id", firm_id)
            .eq("status", "scored")
            .order("scored_at", desc=True)
            .limit(20)
        )
        if analyst:
            sq = sq.eq("uploaded_by", user_id)
        scored_res = sq.execute()

        fq = (
            supabase.table("decks")
            .select("id, company_name, updated_at")
            .eq("firm_id", firm_id)
            .eq("status", "failed")
            .order("updated_at", desc=True)
            .limit(20)
        )
        if analyst:
            fq = fq.eq("uploaded_by", user_id)
        failed_res = fq.execute()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch feed: {str(e)}")

    feed = []
    for d in scored_res.data:
        feed.append({
            "event": "deck_scored",
            "company_name": d.get("company_name"),
            "score": d.get("score"),
            "deck_id": d["id"],
            "timestamp": d.get("scored_at"),
        })
    for d in failed_res.data:
        feed.append({
            "event": "deck_failed",
            "company_name": d.get("company_name"),
            "deck_id": d["id"],
            "timestamp": d.get("updated_at"),
        })

    feed.sort(key=lambda e: e["timestamp"] or "", reverse=True)
    return {"feed": feed[:20]}


# ─── Shared archive query helper ───────────────────────────────

def _archive_query(
    firm_id: str,
    user_id: str,
    analyst: bool,
    scoreboard_status: str,
    sector, stage, score_min, score_max, date_from, date_to,
    page: int,
    limit: int,
):
    query = (
        supabase.table("decks")
        .select("*", count="exact")
        .eq("firm_id", firm_id)
        .eq("scoreboard_status", scoreboard_status)
    )
    if analyst:
        query = query.eq("uploaded_by", user_id)
    if sector:
        query = query.eq("sector", sector)
    if stage:
        query = query.eq("stage", stage)
    if score_min is not None:
        query = query.gte("score", score_min)
    if score_max is not None:
        query = query.lte("score", score_max)
    if date_from:
        query = query.gte("actioned_at", date_from)
    if date_to:
        query = query.lte("actioned_at", date_to)

    offset = (page - 1) * limit
    return query.order("actioned_at", desc=True).range(offset, offset + limit - 1).execute()


def _enrich_actioned_by(rows: list) -> list:
    actor_ids = list({d["actioned_by"] for d in rows if d.get("actioned_by")})
    users_map = {}
    if actor_ids:
        try:
            res = supabase.table("users").select("id, full_name").in_("id", actor_ids).execute()
            users_map = {u["id"]: u["full_name"] for u in res.data}
        except Exception:
            pass
    return [{**d, "actioned_by_name": users_map.get(d.get("actioned_by"), "")} for d in rows]


# ─── Archives: rejected ────────────────────────────────────────

@router.get("/dashboard/archives/rejected")
async def get_rejected(
    sector: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    score_min: Optional[int] = Query(None),
    score_max: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]
    analyst = ctx["member"]["role"] == "analyst"

    try:
        res = _archive_query(firm_id, user_id, analyst, "rejected", sector, stage, score_min, score_max, date_from, date_to, page, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"data": _enrich_actioned_by(res.data), "total": res.count, "page": page, "limit": limit}


# ─── Archives: watchlist ───────────────────────────────────────

@router.get("/dashboard/archives/watchlist")
async def get_watchlist(
    sector: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    score_min: Optional[int] = Query(None),
    score_max: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]
    analyst = ctx["member"]["role"] == "analyst"

    try:
        res = _archive_query(firm_id, user_id, analyst, "watchlist", sector, stage, score_min, score_max, date_from, date_to, page, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"data": _enrich_actioned_by(res.data), "total": res.count, "page": page, "limit": limit}


# ─── Archives: duplicates ──────────────────────────────────────

@router.get("/dashboard/archives/duplicates")
async def get_duplicates(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, original_filename, uploaded_by, created_at, file_hash")
            .eq("firm_id", firm_id)
            .not_.is_("file_hash", "null")
            .order("file_hash")
            .order("created_at")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    hash_groups: dict = {}
    for deck in res.data:
        hash_groups.setdefault(deck["file_hash"], []).append(deck)

    duplicates = []
    for group in hash_groups.values():
        if len(group) < 2:
            continue
        original = group[0]
        for dup in group[1:]:
            duplicates.append({**dup, "duplicate_of": original["id"]})

    uploader_ids = list({d["uploaded_by"] for d in duplicates if d.get("uploaded_by")})
    users_map = {}
    if uploader_ids:
        try:
            users_res = supabase.table("users").select("id, full_name").in_("id", uploader_ids).execute()
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception:
            pass

    data = [{**d, "uploaded_by_name": users_map.get(d.get("uploaded_by"), "")} for d in duplicates]
    total = len(data)
    offset = (page - 1) * limit

    return {"data": data[offset: offset + limit], "total": total, "page": page, "limit": limit}


# ─── Restore rejected deck ─────────────────────────────────────

@router.patch("/dashboard/archives/rejected/{deck_id}/restore")
async def restore_rejected(
    deck_id: str,
    body: RestoreBody,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]

    if body.restore_to not in ("scoreboard", "pipeline"):
        raise HTTPException(status_code=400, detail="restore_to must be scoreboard or pipeline")

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

    if res.data[0]["scoreboard_status"] != "rejected":
        raise HTTPException(status_code=400, detail="Deck is not in rejected status")

    if body.restore_to == "scoreboard":
        try:
            supabase.table("decks").update({
                "scoreboard_status": "active",
                "actioned_by": None,
                "actioned_at": None,
            }).eq("id", deck_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to restore deck: {str(e)}")
    else:
        try:
            supabase.table("decks").update({
                "scoreboard_status": "pipeline",
                "actioned_by": user_id,
                "actioned_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", deck_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update deck: {str(e)}")

        try:
            inbox_id = _get_inbox_column_id(firm_id)
            if inbox_id:
                _create_kanban_card(deck_id, firm_id, inbox_id, user_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create pipeline card: {str(e)}")

    return {"deck_id": deck_id, "restored_to": body.restore_to}


# ─── Watchlist action ──────────────────────────────────────────

@router.patch("/dashboard/archives/watchlist/{deck_id}/action")
async def watchlist_action(
    deck_id: str,
    body: WatchlistActionBody,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]

    if body.action not in ("pipeline", "rejected"):
        raise HTTPException(status_code=400, detail="action must be pipeline or rejected")

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

    if res.data[0]["scoreboard_status"] != "watchlist":
        raise HTTPException(status_code=400, detail="Deck is not in watchlist status")

    try:
        supabase.table("decks").update({
            "scoreboard_status": body.action,
            "actioned_by": user_id,
            "actioned_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update deck: {str(e)}")

    if body.action == "pipeline":
        try:
            inbox_id = _get_inbox_column_id(firm_id)
            if inbox_id:
                _create_kanban_card(deck_id, firm_id, inbox_id, user_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create pipeline card: {str(e)}")

    return {"deck_id": deck_id, "action": body.action}


# ─── Delete duplicate ──────────────────────────────────────────

@router.delete("/dashboard/archives/duplicates/{deck_id}")
async def delete_duplicate(deck_id: str, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    try:
        supabase.table("decks").delete().eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete deck: {str(e)}")

    return {"success": True}
