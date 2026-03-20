from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

def add_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.FRONTEND_URL,
            "https://ptchdeck.com",
            "https://www.ptchdeck.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
