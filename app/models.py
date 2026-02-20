import json
import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(str, Enum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class SignupMode(str, Enum):
    OPEN = "open"
    INVITE_ONLY = "invite_only"


class QuestionnaireVersionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    ANNOTATION = "annotation"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum"), default=Role.USER, index=True
    )
    year_of_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    passkeys: Mapped[list["PasskeyCredential"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list["SessionToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    questionnaire_consents: Mapped[list["QuestionnaireConsent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    owned_questionnaires: Mapped[list["Questionnaire"]] = relationship(
        back_populates="owner_admin",
    )
    responses: Mapped[list["Response"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="passkeys")


class SignupToken(Base):
    __tablename__ = "signup_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(12), index=True)
    role_to_grant: Mapped[Role] = mapped_column(SAEnum(Role, name="token_role_enum"), index=True)
    created_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    used_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    visibility_scope_json: Mapped[str] = mapped_column(Text, default="[]")

    @property
    def questionnaire_version_ids(self) -> list[str]:
        try:
            parsed = json.loads(self.visibility_scope_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, str) and item]


class PendingChallenge(Base):
    __tablename__ = "pending_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    flow: Mapped[str] = mapped_column(String(40), index=True)
    challenge: Mapped[str] = mapped_column(String(120), index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    token_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("signup_tokens.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    details_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserAssignment(Base):
    __tablename__ = "user_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "questionnaire_version_id",
            name="uq_user_questionnaire_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    questionnaire_version_id: Mapped[str] = mapped_column(String(36), index=True)
    granted_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    owner_admin: Mapped[User] = relationship(back_populates="owned_questionnaires")
    versions: Mapped[list["QuestionnaireVersion"]] = relationship(
        back_populates="questionnaire",
        cascade="all, delete-orphan",
    )


class QuestionnaireVersion(Base):
    __tablename__ = "questionnaire_versions"
    __table_args__ = (
        UniqueConstraint("questionnaire_id", "version_number", name="uq_questionnaire_version_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    questionnaire_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questionnaires.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[QuestionnaireVersionStatus] = mapped_column(
        SAEnum(QuestionnaireVersionStatus, name="questionnaire_version_status_enum"),
        default=QuestionnaireVersionStatus.DRAFT,
        index=True,
    )
    instructions_markdown: Mapped[str] = mapped_column(Text, default="")
    consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    questionnaire: Mapped[Questionnaire] = relationship(back_populates="versions")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="questionnaire_version",
        cascade="all, delete-orphan",
    )
    consents: Mapped[list["QuestionnaireConsent"]] = relationship(back_populates="questionnaire_version")
    responses: Mapped[list["Response"]] = relationship(back_populates="questionnaire_version")


class QuestionnaireConsent(Base):
    __tablename__ = "questionnaire_consents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "questionnaire_version_id",
            name="uq_questionnaire_consent_user_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    questionnaire_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), index=True
    )
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="questionnaire_consents")
    questionnaire_version: Mapped[QuestionnaireVersion] = relationship(back_populates="consents")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("questionnaire_version_id", "position", name="uq_question_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    questionnaire_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    prompt_text: Mapped[str] = mapped_column(Text)
    question_type: Mapped[QuestionType] = mapped_column(
        SAEnum(QuestionType, name="question_type_enum"),
        default=QuestionType.SINGLE_CHOICE,
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    questionnaire_version: Mapped[QuestionnaireVersion] = relationship(back_populates="questions")
    choices: Mapped[list["Choice"]] = relationship(back_populates="question", cascade="all, delete-orphan")
    response_items: Mapped[list["ResponseItem"]] = relationship(back_populates="question")


class Choice(Base):
    __tablename__ = "choices"
    __table_args__ = (
        UniqueConstraint("question_id", "position", name="uq_choice_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    question: Mapped[Question] = relationship(back_populates="choices")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    questionnaire_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    original_path: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256_hex: Mapped[str] = mapped_column(String(64), index=True)
    blob_data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = (
        UniqueConstraint("user_id", "questionnaire_version_id", name="uq_response_user_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    questionnaire_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questionnaire_versions.id", ondelete="CASCADE"), index=True
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="responses")
    questionnaire_version: Mapped[QuestionnaireVersion] = relationship(back_populates="responses")
    items: Mapped[list["ResponseItem"]] = relationship(
        back_populates="response",
        cascade="all, delete-orphan",
    )


class ResponseItem(Base):
    __tablename__ = "response_items"
    __table_args__ = (
        UniqueConstraint("response_id", "question_id", name="uq_response_item_question"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    response_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("responses.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    answer_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    response: Mapped[Response] = relationship(back_populates="items")
    question: Mapped[Question] = relationship(back_populates="response_items")
