from fastapi import APIRouter, HTTPException
from app.schemas.contact import ContactRequest, ContactResponse
from app.services.email_service import send_contact_email

router = APIRouter()

@router.post("/contact", response_model=ContactResponse)
async def contact(data: ContactRequest):
    if not data.email or not data.message:
        raise HTTPException(status_code=400, detail="Email and message are required")
    success = send_contact_email(data.name, data.email, data.message)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send email")
    return ContactResponse(success=True, message="Message sent successfully")
