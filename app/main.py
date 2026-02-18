from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.routers import admin, auth, passkeys
from app.services.settings_service import ensure_default_settings

settings = get_settings()


def _parse_allowed_origins() -> list[str]:
    if settings.cors_allow_origins:
        values = [item.strip() for item in settings.cors_allow_origins.split(",")]
        origins = [item for item in values if item]
        if origins:
            return origins
    return [settings.webauthn_origin]


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_default_settings(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Medirator API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


app.include_router(auth.router)
app.include_router(passkeys.router)
app.include_router(admin.router)
if settings.enable_builtin_ui:
    from app.routers import pages

    app.include_router(pages.router)
