import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger("medirater")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
from app.db import Base, SessionLocal, engine
from app.routers import admin, assets, auth, passkeys, questionnaires, user_questionnaires
from app.services.schema_service import ensure_runtime_schema
from app.services.settings_service import ensure_default_settings

settings = get_settings()


def _parse_allowed_origins() -> list[str]:
    if settings.cors_allow_origins:
        values = [item.strip() for item in settings.cors_allow_origins.split(",")]
        origins = [item for item in values if item]
        if origins:
            return origins
    return [settings.webauthn_origin]


_PLACEHOLDER_PEPPERS = frozenset(
    {
        "change-me-before-production",
        "replace-with-a-long-random-secret",
    }
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.token_pepper in _PLACEHOLDER_PEPPERS:
        logger.warning(
            "SECURITY WARNING: APP_TOKEN_PEPPER is set to the default value. "
            "Set a unique, secret value via the APP_TOKEN_PEPPER environment variable "
            "before deploying to production."
        )
    if settings.insecure_dev_webauthn:
        logger.warning(
            "SECURITY WARNING: APP_INSECURE_DEV_WEBAUTHN is enabled. "
            "WebAuthn cryptographic verification is completely disabled. "
            "This MUST be turned off in production."
        )
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(engine)
    db = SessionLocal()
    try:
        ensure_default_settings(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Medirater API", version="0.1.0", lifespan=lifespan)

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
app.include_router(assets.router)
app.include_router(questionnaires.router)
app.include_router(user_questionnaires.router)
if settings.enable_builtin_ui:
    from app.routers import pages

    app.include_router(pages.router)
