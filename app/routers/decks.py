import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query

from app.middleware.auth import all_members
from app.services.supabase_client import supabase

router = APIRouter()

ALLOWED_FORMATS = {"pdf", "pptx", "docx"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


# ─── Upload deck ───────────────────────────────────────────────

@router.post("/decks/upload")
async def upload_deck(
    file: UploadFile = File(...),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]

    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format. Allowed: {', '.join(sorted(ALLOWED_FORMATS))}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 25 MB limit")

    file_hash = hashlib.sha256(contents).hexdigest()
    try:
        dup = (
            supabase.table("decks")
            .select("id, original_filename, uploaded_by")
            .eq("firm_id", firm_id)
            .eq("file_hash", file_hash)
            .execute()
        )
        if dup.data:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate",
                    "message": "This file was already uploaded",
                    "existing_deck_id": dup.data[0]["id"],
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Duplicate check failed: {str(e)}")

    deck_id = str(uuid.uuid4())
    storage_path = f"{firm_id}/{deck_id}/original.{ext}"

    try:
        supabase.storage.from_("pitch-decks").upload(
            path=storage_path,
            file=contents,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {str(e)}")

    try:
        supabase.table("decks").insert({
            "id": deck_id,
            "firm_id": firm_id,
            "uploaded_by": user_id,
            "original_filename": file.filename,
            "file_url": storage_path,
            "file_format": ext,
            "status": "queued",
            "file_hash": file_hash,
        }).execute()
    except Exception as e:
        try:
            supabase.storage.from_("pitch-decks").remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Database insert failed: {str(e)}")

    return {"deck_id": deck_id, "status": "queued"}


# ─── List decks ────────────────────────────────────────────────

@router.get("/decks")
async def list_decks(
    status: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    uploaded_by: Optional[str] = Query(None),
    date_range: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        query = (
            supabase.table("decks")
            .select("*", count="exact")
            .eq("firm_id", firm_id)
        )

        if status:
            query = query.eq("status", status)
        if sector:
            query = query.eq("sector", sector)
        if stage:
            query = query.eq("stage", stage)
        if uploaded_by:
            query = query.eq("uploaded_by", uploaded_by)
        if date_range:
            if date_range == "7days":
                cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            elif date_range == "30days":
                cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
            else:
                raise HTTPException(status_code=400, detail="date_range must be 7days or 30days")
            query = query.gte("created_at", cutoff)

        offset = (page - 1) * limit
        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        # Enrich with uploader names
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
        raise HTTPException(status_code=500, detail=f"Failed to fetch decks: {str(e)}")


# ─── Deck status ───────────────────────────────────────────────

@router.get("/decks/{deck_id}/status")
async def get_deck_status(
    deck_id: str,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, status, error_message")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    deck = res.data[0]
    return {
        "deck_id": deck["id"],
        "status": deck["status"],
        "error_message": deck.get("error_message"),
    }


# ─── Retry failed deck ─────────────────────────────────────────

@router.post("/decks/{deck_id}/retry")
async def retry_deck(
    deck_id: str,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, status")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    if res.data[0]["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only failed decks can be retried")

    try:
        supabase.table("decks").update({"status": "queued"}).eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")

    return {"deck_id": deck_id, "status": "queued"}


# ─── Analyze deck ──────────────────────────────────────────────

@router.post("/decks/{deck_id}/analyze")
async def analyze_deck(
    deck_id: str,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, status")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    if res.data[0]["status"] != "queued":
        raise HTTPException(status_code=400, detail="Deck must be in queued status to analyze")

    try:
        supabase.table("decks").update({"status": "processing"}).eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")

    return {"deck_id": deck_id, "status": "processing"}


# ─── Score deck ────────────────────────────────────────────────

@router.post("/decks/{deck_id}/score")
async def score_deck(
    deck_id: str,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, status")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    if res.data[0]["status"] != "processing":
        raise HTTPException(status_code=400, detail="Deck must be in processing status to score")

    try:
        supabase.table("decks").update({
            "status": "scored",
            "score": 7,
            "scored_at": datetime.utcnow().isoformat(),
        }).eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")

    return {"deck_id": deck_id, "status": "scored", "score": 7}


# ─── Delete deck ───────────────────────────────────────────────

@router.delete("/decks/{deck_id}")
async def delete_deck(
    deck_id: str,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("decks")
            .select("id, file_url")
            .eq("id", deck_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Deck not found")

    file_url = res.data[0]["file_url"]

    try:
        supabase.storage.from_("pitch-decks").remove([file_url])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage delete failed: {str(e)}")

    try:
        supabase.table("decks").delete().eq("id", deck_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database delete failed: {str(e)}")

    return {"success": True}
