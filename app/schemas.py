from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Role, SignupMode


class SignupBeginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    token: str | None = None


class SignupCompleteRequest(BaseModel):
    challenge_id: str
    credential: dict[str, Any]


class LoginBeginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)


class LoginCompleteRequest(BaseModel):
    challenge_id: str
    credential: dict[str, Any]


class BeginPasskeyAddRequest(BaseModel):
    label: str | None = None


class CompletePasskeyAddRequest(BaseModel):
    challenge_id: str
    credential: dict[str, Any]
    label: str | None = None


class PasskeyRenameRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class WebAuthnBeginResponse(BaseModel):
    challenge_id: str
    public_key: dict[str, Any]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: Role
    created_at: datetime


class AuthSessionResponse(BaseModel):
    user: UserOut
    session_expires_at: datetime


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    credential_id: str
    label: str
    sign_count: int
    created_at: datetime
    last_used_at: datetime | None


class SignupModeResponse(BaseModel):
    mode: SignupMode


class UpdateSignupModeRequest(BaseModel):
    mode: SignupMode


class CreateSignupTokenRequest(BaseModel):
    role: Role
    expires_in_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    questionnaire_version_ids: list[str] = Field(default_factory=list)


class CreateSignupTokenResponse(BaseModel):
    token: str
    token_hint: str
    role: Role
    expires_at: datetime
    questionnaire_version_ids: list[str] = Field(default_factory=list)


class SignupTokenSummary(BaseModel):
    id: str
    token_hint: str
    role_to_grant: Role
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    questionnaire_version_ids: list[str] = Field(default_factory=list)


class SignupTokenListResponse(BaseModel):
    items: list[SignupTokenSummary]
