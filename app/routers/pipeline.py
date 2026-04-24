import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import all_members, admin_and_above
from app.services.supabase_client import supabase

router = APIRouter()


class MoveCardBody(BaseModel):
    column_id: str
    position: int


class FinalCardBody(BaseModel):
    action: str  # "invested" | "rejected"


class CreateColumnBody(BaseModel):
    name: str


class RenameColumnBody(BaseModel):
    name: str


class ReorderItem(BaseModel):
    id: str
    position: int


class ReorderBody(BaseModel):
    columns: list[ReorderItem]


def _days_in_column(entered_at_str: str) -> int:
    try:
        entered_at = datetime.fromisoformat(entered_at_str.replace("Z", "+00:00"))
        if entered_at.tzinfo is None:
            entered_at = entered_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - entered_at).days
    except Exception:
        return 0


# ─── Get pipeline ──────────────────────────────────────────────

@router.get("/pipeline")
async def get_pipeline(ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]

    try:
        cols_res = (
            supabase.table("pipeline_columns")
            .select("id, name, position, is_fixed")
            .eq("firm_id", firm_id)
            .order("position")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        cards_res = (
            supabase.table("kanban_cards")
            .select("id, deck_id, column_id, position, moved_by, entered_column_at")
            .eq("firm_id", firm_id)
            .eq("final_status", "active")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    deck_ids = list({c["deck_id"] for c in cards_res.data if c.get("deck_id")})
    decks_map = {}
    if deck_ids:
        try:
            decks_res = (
                supabase.table("decks")
                .select("id, company_name, score, sector, stage")
                .in_("id", deck_ids)
                .execute()
            )
            decks_map = {d["id"]: d for d in decks_res.data}
        except Exception:
            pass

    mover_ids = list({c["moved_by"] for c in cards_res.data if c.get("moved_by")})
    users_map = {}
    if mover_ids:
        try:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", mover_ids)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception:
            pass

    cards_by_column: dict = {}
    for card in sorted(cards_res.data, key=lambda c: c.get("position", 0)):
        deck = decks_map.get(card["deck_id"], {})
        enriched = {
            "id": card["id"],
            "deck_id": card["deck_id"],
            "company_name": deck.get("company_name"),
            "score": deck.get("score"),
            "sector": deck.get("sector"),
            "stage": deck.get("stage"),
            "moved_by_name": users_map.get(card.get("moved_by"), ""),
            "days_in_column": _days_in_column(card["entered_column_at"]) if card.get("entered_column_at") else 0,
            "entered_column_at": card["entered_column_at"],
        }
        cards_by_column.setdefault(card["column_id"], []).append(enriched)

    return {
        "columns": [
            {
                "id": col["id"],
                "name": col["name"],
                "position": col["position"],
                "is_fixed": col["is_fixed"],
                "cards": cards_by_column.get(col["id"], []),
            }
            for col in cols_res.data
        ]
    }


# ─── Move card ─────────────────────────────────────────────────

@router.patch("/pipeline/cards/{card_id}/move")
async def move_card(
    card_id: str,
    body: MoveCardBody,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("kanban_cards")
            .select("id")
            .eq("id", card_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Card not found")

    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("kanban_cards").update({
            "column_id": body.column_id,
            "position": body.position,
            "moved_by": ctx["user"]["id"],
            "moved_at": now,
            "entered_column_at": now,
        }).eq("id", card_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move card: {str(e)}")

    return {"card_id": card_id, "column_id": body.column_id, "position": body.position}


# ─── Finalize card ─────────────────────────────────────────────

@router.patch("/pipeline/cards/{card_id}/final")
async def finalize_card(
    card_id: str,
    body: FinalCardBody,
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    if body.action not in ("invested", "rejected"):
        raise HTTPException(status_code=400, detail="action must be invested or rejected")

    try:
        res = (
            supabase.table("kanban_cards")
            .select("id, deck_id")
            .eq("id", card_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Card not found")

    deck_id = res.data[0]["deck_id"]
    now = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("kanban_cards").update({
            "final_status": body.action,
            "moved_by": ctx["user"]["id"],
            "moved_at": now,
        }).eq("id", card_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update card: {str(e)}")

    if body.action == "rejected":
        try:
            supabase.table("decks").update({
                "scoreboard_status": "rejected",
                "actioned_by": ctx["user"]["id"],
                "actioned_at": now,
            }).eq("id", deck_id).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update deck: {str(e)}")

        return {"card_id": card_id, "final_status": body.action}

    if body.action == "invested":
        try:
            deck_res = (
                supabase.table("decks")
                .select("company_name, sector, stage")
                .eq("id", deck_id)
                .execute()
            )
            deck = deck_res.data[0] if deck_res.data else {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch deck: {str(e)}")

        portfolio_id = str(uuid.uuid4())
        try:
            supabase.table("portfolio_companies").insert({
                "id": portfolio_id,
                "firm_id": firm_id,
                "deck_id": deck_id,
                "source": "pipeline",
                "status": "draft",
                "added_by": ctx["user"]["id"],
                "company_name": deck.get("company_name") or "Unknown",
                "sector": deck.get("sector"),
                "stage_at_investment": deck.get("stage"),
            }).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create portfolio entry: {str(e)}")

        return {"card_id": card_id, "final_status": "invested", "portfolio_id": portfolio_id}

    return {"card_id": card_id, "final_status": body.action}


# ─── Create column ─────────────────────────────────────────────

@router.post("/pipeline/columns")
async def create_column(body: CreateColumnBody, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        max_res = (
            supabase.table("pipeline_columns")
            .select("position")
            .eq("firm_id", firm_id)
            .order("position", desc=True)
            .limit(1)
            .execute()
        )
        next_position = (max_res.data[0]["position"] + 1) if max_res.data else 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        res = supabase.table("pipeline_columns").insert({
            "firm_id": firm_id,
            "name": body.name,
            "position": next_position,
            "is_fixed": False,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create column: {str(e)}")

    return res.data[0]


# ─── Reorder columns — must be defined BEFORE /{column_id} ────

@router.patch("/pipeline/columns/reorder")
async def reorder_columns(body: ReorderBody, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    column_ids = [item.id for item in body.columns]

    try:
        res = (
            supabase.table("pipeline_columns")
            .select("id, is_fixed")
            .eq("firm_id", firm_id)
            .in_("id", column_ids)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    fixed = [c["id"] for c in res.data if c["is_fixed"]]
    if fixed:
        raise HTTPException(status_code=400, detail="Cannot change position of fixed columns")

    try:
        for item in body.columns:
            supabase.table("pipeline_columns") \
                .update({"position": item.position}) \
                .eq("id", item.id) \
                .eq("firm_id", firm_id) \
                .execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reorder failed: {str(e)}")

    return {"success": True}


# ─── Rename column ─────────────────────────────────────────────

@router.patch("/pipeline/columns/{column_id}")
async def rename_column(
    column_id: str,
    body: RenameColumnBody,
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("pipeline_columns")
            .select("id, is_fixed")
            .eq("id", column_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Column not found")

    if res.data[0]["is_fixed"]:
        raise HTTPException(status_code=400, detail="Cannot rename a fixed column")

    try:
        updated = (
            supabase.table("pipeline_columns")
            .update({"name": body.name})
            .eq("id", column_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename column: {str(e)}")

    return updated.data[0]


# ─── Delete column ─────────────────────────────────────────────

@router.delete("/pipeline/columns/{column_id}")
async def delete_column(column_id: str, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("pipeline_columns")
            .select("id, is_fixed")
            .eq("id", column_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Column not found")

    if res.data[0]["is_fixed"]:
        raise HTTPException(status_code=400, detail="Cannot delete a fixed column")

    try:
        cards_res = (
            supabase.table("kanban_cards")
            .select("id", count="exact")
            .eq("column_id", column_id)
            .eq("final_status", "active")
            .execute()
        )
        if cards_res.count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Column has {cards_res.count} active card(s). Move them before deleting.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        supabase.table("pipeline_columns").delete().eq("id", column_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete column: {str(e)}")

    return {"success": True}
