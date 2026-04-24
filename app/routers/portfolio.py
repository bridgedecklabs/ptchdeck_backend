import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.middleware.auth import all_members, admin_and_above, owner_only
from app.services.supabase_client import supabase

router = APIRouter()

SENSITIVE_FIELDS = {"check_size", "ownership_percent", "valuation_at_entry", "return_multiple"}

SORT_MAP = {
    "investment_date_desc": ("investment_date", True),
    "company_name_asc":     ("company_name",    False),
    "check_size_desc":      ("check_size",       True),
}


def _strip_sensitive(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k not in SENSITIVE_FIELDS}


class PortfolioCreateBody(BaseModel):
    company_name: str
    sector: str
    stage_at_investment: str
    investment_date: str
    current_status: str
    check_size: Optional[float] = None
    ownership_percent: Optional[float] = None
    valuation_at_entry: Optional[float] = None
    return_multiple: Optional[float] = None
    website: Optional[str] = None
    description: Optional[str] = None
    lead_partner: Optional[str] = None
    co_investors: Optional[str] = None


class PortfolioUpdateBody(BaseModel):
    company_name: Optional[str] = None
    sector: Optional[str] = None
    stage_at_investment: Optional[str] = None
    investment_date: Optional[str] = None
    current_status: Optional[str] = None
    check_size: Optional[float] = None
    ownership_percent: Optional[float] = None
    valuation_at_entry: Optional[float] = None
    return_multiple: Optional[float] = None
    website: Optional[str] = None
    description: Optional[str] = None
    lead_partner: Optional[str] = None
    co_investors: Optional[str] = None


# ─── List portfolio ────────────────────────────────────────────

@router.get("/portfolio")
async def list_portfolio(
    sector: Optional[str] = Query(None),
    stage_at_investment: Optional[str] = Query(None),
    current_status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    sort_by: str = Query("investment_date_desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    role = ctx["member"]["role"]

    try:
        query = (
            supabase.table("portfolio_companies")
            .select("*", count="exact")
            .eq("firm_id", firm_id)
        )

        if sector:
            query = query.eq("sector", sector)
        if stage_at_investment:
            query = query.eq("stage_at_investment", stage_at_investment)
        if current_status:
            query = query.eq("current_status", current_status)
        if source:
            query = query.eq("source", source)
        if year:
            query = query.gte("investment_date", f"{year}-01-01").lte("investment_date", f"{year}-12-31")

        sort_col, sort_desc = SORT_MAP.get(sort_by, ("investment_date", True))
        offset = (page - 1) * limit
        res = query.order(sort_col, desc=sort_desc).range(offset, offset + limit - 1).execute()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    adder_ids = list({e["added_by"] for e in res.data if e.get("added_by")})
    users_map = {}
    if adder_ids:
        try:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", adder_ids)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception:
            pass

    data = []
    for entry in res.data:
        enriched = {**entry, "added_by_name": users_map.get(entry.get("added_by"), "")}
        if role == "analyst":
            enriched = _strip_sensitive(enriched)
        data.append(enriched)

    return {"data": data, "total": res.count, "page": page, "limit": limit}


# ─── Create portfolio entry ────────────────────────────────────

@router.post("/portfolio")
async def create_portfolio(body: PortfolioCreateBody, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = supabase.table("portfolio_companies").insert({
            "id": str(uuid.uuid4()),
            "firm_id": firm_id,
            "added_by": ctx["user"]["id"],
            "source": "manual",
            "status": "active",
            **body.model_dump(exclude_none=True),
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create portfolio entry: {str(e)}")

    return res.data[0]


# ─── Get portfolio entry ───────────────────────────────────────

@router.get("/portfolio/{portfolio_id}")
async def get_portfolio(portfolio_id: str, ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]
    role = ctx["member"]["role"]

    try:
        res = (
            supabase.table("portfolio_companies")
            .select("*")
            .eq("id", portfolio_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")

    entry = res.data[0]
    if role == "analyst":
        entry = _strip_sensitive(entry)

    return entry


# ─── Update portfolio entry ────────────────────────────────────

@router.patch("/portfolio/{portfolio_id}")
async def update_portfolio(
    portfolio_id: str,
    body: PortfolioUpdateBody,
    ctx: dict = Depends(admin_and_above),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("portfolio_companies")
            .select("id")
            .eq("id", portfolio_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        updated = (
            supabase.table("portfolio_companies")
            .update(updates)
            .eq("id", portfolio_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update entry: {str(e)}")

    return updated.data[0]


# ─── Publish portfolio entry ───────────────────────────────────

@router.patch("/portfolio/{portfolio_id}/publish")
async def publish_portfolio(portfolio_id: str, ctx: dict = Depends(admin_and_above)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("portfolio_companies")
            .select("id")
            .eq("id", portfolio_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")

    try:
        supabase.table("portfolio_companies").update({"status": "active"}).eq("id", portfolio_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish entry: {str(e)}")

    return {"portfolio_id": portfolio_id, "status": "active"}


# ─── Delete portfolio entry ────────────────────────────────────

@router.delete("/portfolio/{portfolio_id}")
async def delete_portfolio(portfolio_id: str, ctx: dict = Depends(owner_only)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("portfolio_companies")
            .select("id")
            .eq("id", portfolio_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Portfolio entry not found")

    try:
        supabase.table("portfolio_companies").delete().eq("id", portfolio_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete entry: {str(e)}")

    return {"success": True}
