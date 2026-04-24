import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel

from app.middleware.auth import all_members, owner_only
from app.services.supabase_client import supabase
from app.services.email_service import send_application_confirmation_email

router = APIRouter()

ALLOWED_FORMATS = {"pdf", "pptx", "docx"}
MAX_FILE_SIZE = 25 * 1024 * 1024

VALID_STATUS_TRANSITIONS = {
    "draft":  "active",
    "active": "closed",
    "closed": "archived",
}


def _make_slug(name: str, firm_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug}-{firm_id[:8]}"


def _get_submission_counts(cohort_ids: list) -> dict:
    if not cohort_ids:
        return {}
    res = (
        supabase.table("cohort_submissions")
        .select("cohort_id")
        .in_("cohort_id", cohort_ids)
        .execute()
    )
    counts: dict = {}
    for s in res.data:
        counts[s["cohort_id"]] = counts.get(s["cohort_id"], 0) + 1
    return counts


class CohortCreateBody(BaseModel):
    name: str
    description: Optional[str] = None
    sector_focus: Optional[str] = None
    stage_focus: Optional[str] = None
    deadline: Optional[str] = None
    max_applications: Optional[int] = None


class CohortUpdateBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sector_focus: Optional[str] = None
    stage_focus: Optional[str] = None
    deadline: Optional[str] = None
    max_applications: Optional[int] = None


class StatusBody(BaseModel):
    status: str


# ─── List cohorts ──────────────────────────────────────────────

@router.get("/cohorts")
async def list_cohorts(ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("cohorts")
            .select("*")
            .eq("firm_id", firm_id)
            .neq("status", "archived")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    cohort_ids = [c["id"] for c in res.data]

    try:
        counts = _get_submission_counts(cohort_ids)
    except Exception:
        counts = {}

    creator_ids = list({c["created_by"] for c in res.data if c.get("created_by")})
    users_map = {}
    if creator_ids:
        try:
            users_res = (
                supabase.table("users")
                .select("id, full_name")
                .in_("id", creator_ids)
                .execute()
            )
            users_map = {u["id"]: u["full_name"] for u in users_res.data}
        except Exception:
            pass

    return [
        {
            **c,
            "submission_count": counts.get(c["id"], 0),
            "created_by_name": users_map.get(c.get("created_by"), ""),
        }
        for c in res.data
    ]


# ─── Create cohort ─────────────────────────────────────────────

@router.post("/cohorts")
async def create_cohort(body: CohortCreateBody, ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]
    user_id = ctx["user"]["id"]

    slug = _make_slug(body.name, firm_id)

    try:
        res = supabase.table("cohorts").insert({
            "firm_id": firm_id,
            "created_by": user_id,
            "name": body.name,
            "description": body.description,
            "slug": slug,
            "status": "draft",
            "sector_focus": body.sector_focus,
            "stage_focus": body.stage_focus,
            "deadline": body.deadline,
            "max_applications": body.max_applications,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create cohort: {str(e)}")

    return {**res.data[0], "submission_count": 0}


# ─── Get cohort ────────────────────────────────────────────────

@router.get("/cohorts/{cohort_id}")
async def get_cohort(cohort_id: str, ctx: dict = Depends(all_members)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("cohorts")
            .select("*")
            .eq("id", cohort_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    try:
        counts = _get_submission_counts([cohort_id])
    except Exception:
        counts = {}

    return {**res.data[0], "submission_count": counts.get(cohort_id, 0)}


# ─── Update cohort ─────────────────────────────────────────────

@router.patch("/cohorts/{cohort_id}")
async def update_cohort(
    cohort_id: str,
    body: CohortUpdateBody,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("cohorts")
            .select("id, status")
            .eq("id", cohort_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if res.data[0]["status"] in ("closed", "archived"):
        raise HTTPException(status_code=400, detail="Cannot edit a closed or archived cohort")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        updated = supabase.table("cohorts").update(updates).eq("id", cohort_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update cohort: {str(e)}")

    return updated.data[0]


# ─── Update cohort status ──────────────────────────────────────

@router.patch("/cohorts/{cohort_id}/status")
async def update_cohort_status(
    cohort_id: str,
    body: StatusBody,
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("cohorts")
            .select("id, status")
            .eq("id", cohort_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    current_status = res.data[0]["status"]
    allowed_next = VALID_STATUS_TRANSITIONS.get(current_status)

    if body.status != allowed_next:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition: {current_status} → {body.status}. Allowed next: {allowed_next}",
        )

    update_data: dict = {"status": body.status}
    if body.status == "closed":
        update_data["closed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("cohorts").update(update_data).eq("id", cohort_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)}")

    return {"cohort_id": cohort_id, "status": body.status}


# ─── Delete cohort ─────────────────────────────────────────────

@router.delete("/cohorts/{cohort_id}")
async def delete_cohort(cohort_id: str, ctx: dict = Depends(owner_only)):
    firm_id = ctx["firm"]["id"]

    try:
        res = (
            supabase.table("cohorts")
            .select("id, status")
            .eq("id", cohort_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if res.data[0]["status"] != "draft":
        raise HTTPException(status_code=400, detail="Only draft cohorts can be deleted")

    try:
        supabase.table("cohorts").delete().eq("id", cohort_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete cohort: {str(e)}")

    return {"success": True}


# ─── List submissions ──────────────────────────────────────────

@router.get("/cohorts/{cohort_id}/submissions")
async def list_submissions(
    cohort_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    ctx: dict = Depends(all_members),
):
    firm_id = ctx["firm"]["id"]

    try:
        cohort_res = (
            supabase.table("cohorts")
            .select("id")
            .eq("id", cohort_id)
            .eq("firm_id", firm_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not cohort_res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    try:
        offset = (page - 1) * limit
        res = (
            supabase.table("cohort_submissions")
            .select("*", count="exact")
            .eq("cohort_id", cohort_id)
            .order("submitted_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"data": res.data, "total": res.count, "page": page, "limit": limit}


# ─── Public: cohort info ───────────────────────────────────────

@router.get("/apply/{slug}")
async def get_apply_info(slug: str):
    try:
        res = (
            supabase.table("cohorts")
            .select("name, description, sector_focus, stage_focus, deadline, status")
            .eq("slug", slug)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    cohort = res.data[0]
    if cohort["status"] != "active":
        raise HTTPException(status_code=400, detail="This cohort is not currently accepting applications")

    return cohort


# ─── Public: submit application ───────────────────────────────

@router.post("/apply/{slug}")
async def submit_application(
    slug: str,
    founder_name: str = Form(...),
    founder_email: str = Form(...),
    company_name: str = Form(...),
    file: UploadFile = File(...),
    answers: Optional[str] = Form(None),
):
    try:
        cohort_res = (
            supabase.table("cohorts")
            .select("id, firm_id, status, max_applications, name")
            .eq("slug", slug)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not cohort_res.data:
        raise HTTPException(status_code=404, detail="Cohort not found")

    cohort = cohort_res.data[0]

    if cohort["status"] != "active":
        raise HTTPException(status_code=400, detail="This cohort is not currently accepting applications")

    if cohort["max_applications"] is not None:
        try:
            count_res = (
                supabase.table("cohort_submissions")
                .select("id", count="exact")
                .eq("cohort_id", cohort["id"])
                .execute()
            )
            if count_res.count >= cohort["max_applications"]:
                raise HTTPException(status_code=400, detail="This cohort has reached its maximum number of applications")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format. Allowed: {', '.join(sorted(ALLOWED_FORMATS))}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 25 MB limit")

    try:
        owner_res = (
            supabase.table("firm_members")
            .select("user_id")
            .eq("firm_id", cohort["firm_id"])
            .eq("role", "owner")
            .eq("status", "active")
            .execute()
        )
        if not owner_res.data:
            raise HTTPException(status_code=500, detail="Could not resolve firm owner for submission")
        owner_user_id = owner_res.data[0]["user_id"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resolve firm owner: {str(e)}")

    submission_id = str(uuid.uuid4())
    deck_id = str(uuid.uuid4())
    storage_path = f"{cohort['id']}/{submission_id}/original.{ext}"

    try:
        supabase.storage.from_("cohort-decks").upload(
            path=storage_path,
            file=contents,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

    try:
        supabase.table("decks").insert({
            "id": deck_id,
            "firm_id": cohort["firm_id"],
            "cohort_id": cohort["id"],
            "uploaded_by": owner_user_id,
            "original_filename": file.filename,
            "file_url": storage_path,
            "file_format": ext,
            "status": "queued",
        }).execute()
    except Exception as e:
        try:
            supabase.storage.from_("cohort-decks").remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to save deck: {str(e)}")

    try:
        supabase.table("cohort_submissions").insert({
            "id": submission_id,
            "cohort_id": cohort["id"],
            "firm_id": cohort["firm_id"],
            "deck_id": deck_id,
            "founder_name": founder_name,
            "founder_email": founder_email,
            "company_name": company_name,
            "answers": answers,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save submission: {str(e)}")

    try:
        send_application_confirmation_email(
            founder_email=founder_email,
            founder_name=founder_name,
            company_name=company_name,
            cohort_name=cohort["name"],
        )
    except Exception as e:
        print(f"Confirmation email failed (submission saved): {e}")

    return {"success": True, "message": "Application received"}
