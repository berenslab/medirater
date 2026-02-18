from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import QuestionType, QuestionnaireVersionStatus, Role, SignupMode


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


class AdminManagedUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: Role
    is_active: bool
    created_at: datetime


class AdminUpdateUserRequest(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class ChoiceCreate(BaseModel):
    position: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=500)
    value: str = Field(min_length=1, max_length=120)


class ChoiceUpdate(BaseModel):
    position: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=500)
    value: str = Field(min_length=1, max_length=120)


class ChoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int
    label: str
    value: str


class QuestionCreate(BaseModel):
    position: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)
    question_type: QuestionType
    is_required: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    choices: list[ChoiceCreate] = Field(default_factory=list)


class QuestionUpdate(BaseModel):
    position: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)
    question_type: QuestionType
    is_required: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    choices: list[ChoiceUpdate] = Field(default_factory=list)


class QuestionOut(BaseModel):
    id: str
    position: int
    prompt_text: str
    question_type: QuestionType
    is_required: bool
    config: dict[str, Any]
    choices: list[ChoiceOut]


class QuestionnaireCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    instructions_markdown: str = ""


class QuestionnaireUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class QuestionnaireVersionCreateRequest(BaseModel):
    instructions_markdown: str = ""


class QuestionnaireVersionUpdateRequest(BaseModel):
    instructions_markdown: str = ""


class QuestionnaireSummaryOut(BaseModel):
    id: str
    owner_admin_id: str
    title: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    latest_version_id: str | None
    latest_version_status: QuestionnaireVersionStatus | None
    latest_version_number: int | None


class QuestionnaireVersionSummaryOut(BaseModel):
    id: str
    questionnaire_id: str
    version_number: int
    status: QuestionnaireVersionStatus
    instructions_markdown: str
    created_by_id: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class QuestionnaireVersionDetailOut(QuestionnaireVersionSummaryOut):
    questions: list[QuestionOut]


class QuestionnaireDetailOut(BaseModel):
    id: str
    owner_admin_id: str
    title: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    versions: list[QuestionnaireVersionSummaryOut]


class AssetOut(BaseModel):
    id: str
    owner_user_id: str
    file_name: str
    original_path: str | None
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    sha256_hex: str
    created_at: datetime


class BulkRecipeType(str, Enum):
    SINGLE_PER_FILE = "single_per_file"
    TRIPLET_BY_SUFFIX = "triplet_by_suffix"
    PAIRED_BY_FILENAME = "paired_by_filename"
    CASE_WITH_PATCHES = "case_with_patches"


class BulkTemplateChoice(BaseModel):
    label: str = Field(min_length=1, max_length=500)
    value: str = Field(min_length=1, max_length=120)


class BulkRecipeQuestionTemplate(BaseModel):
    prompt_template: str = Field(min_length=1)
    question_type: QuestionType
    is_required: bool = True
    choices: list[BulkTemplateChoice] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class BulkRecipePreviewRequest(BaseModel):
    recipe_type: BulkRecipeType
    asset_ids: list[str] = Field(min_length=1)
    recipe_config: dict[str, Any] = Field(default_factory=dict)
    question_templates: list[BulkRecipeQuestionTemplate] = Field(default_factory=list)
    patch_question_template: BulkRecipeQuestionTemplate | None = None


class BulkGeneratedQuestionPreview(BaseModel):
    prompt_text: str
    question_type: QuestionType
    is_required: bool
    choices: list[BulkTemplateChoice] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class BulkRecipeCasePreview(BaseModel):
    case_key: str
    stimulus_asset_ids: list[str] = Field(default_factory=list)
    patch_asset_ids: list[str] = Field(default_factory=list)
    questions: list[BulkGeneratedQuestionPreview] = Field(default_factory=list)


class BulkRecipePreviewResponse(BaseModel):
    cases: list[BulkRecipeCasePreview]
    warnings: list[str] = Field(default_factory=list)


class BulkRecipeApplyRequest(BulkRecipePreviewRequest):
    replace_existing_questions: bool = False


class BulkRecipeApplyPreviewRequest(BaseModel):
    cases: list[BulkRecipeCasePreview] = Field(min_length=1)
    replace_existing_questions: bool = False


class BulkRecipeApplyResponse(BaseModel):
    cases: int
    created_questions: int
    warnings: list[str] = Field(default_factory=list)
