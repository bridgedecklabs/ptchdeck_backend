import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
from fastapi import HTTPException
from app.config import settings
from pathlib import Path


def _init_app():
    if not firebase_admin._apps:
        BASE_DIR = Path(__file__).resolve().parent.parent.parent
        cert_path = BASE_DIR / settings.FIREBASE_SERVICE_ACCOUNT_PATH
        cred = credentials.Certificate(str(cert_path))
        firebase_admin.initialize_app(cred)


_init_app()


def verify_token(token: str) -> str:
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")