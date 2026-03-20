from fastapi import FastAPI
from app.middleware.cors import add_cors
from app.routers import contact

app = FastAPI(
    title="PtchDeck API",
    version="1.0.0",
    docs_url="/docs",
)

add_cors(app)
app.include_router(contact.router, prefix="/api")

@app.get("/health")
async def health():
    return {"status": "ok"}
