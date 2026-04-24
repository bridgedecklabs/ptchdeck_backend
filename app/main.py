from fastapi import FastAPI
from app.middleware.cors import add_cors
from app.routers import contact, auth, firm, decks
from app.config import settings

is_dev = settings.ENVIRONMENT == "development"

app = FastAPI(
    title="PtchDeck API",
    version="1.0.0",
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
)

add_cors(app)
app.include_router(contact.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(firm.router, prefix="/api")
app.include_router(decks.router, prefix="/api")

@app.get("/health")
async def health():
    return {"status": "ok"}
