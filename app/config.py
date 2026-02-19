from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    database_url: str = "sqlite:///./app.db"
    token_pepper: str = "change-me-before-production"
    session_cookie_name: str = "session_token"
    session_ttl_hours: int = 24 * 14
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_cookie_domain: str | None = None
    challenge_ttl_minutes: int = 10
    enable_builtin_ui: bool = True
    cors_allow_origins: str | None = None

    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Medirater"
    webauthn_origin: str = "http://localhost:8000"

    # Prototype mode: credential payloads are accepted without full WebAuthn cryptographic verification.
    insecure_dev_webauthn: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
