from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_admin_user, get_superadmin_user
from app.models import Role, SignupToken, User
from app.schemas import (
    CreateSignupTokenRequest,
    CreateSignupTokenResponse,
    SignupModeResponse,
    SignupTokenListResponse,
    SignupTokenSummary,
    UpdateSignupModeRequest,
)
from app.services.settings_service import get_public_signup_mode, set_public_signup_mode
from app.services.token_service import deserialize_visibility_scope, issue_signup_token

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/settings/public-signup-mode", response_model=SignupModeResponse)
def get_signup_mode(
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> SignupModeResponse:
    return SignupModeResponse(mode=get_public_signup_mode(db))


@router.put("/settings/public-signup-mode", response_model=SignupModeResponse)
def update_signup_mode(
    payload: UpdateSignupModeRequest,
    current_user: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
) -> SignupModeResponse:
    mode = set_public_signup_mode(db, mode=payload.mode, actor_user_id=current_user.id)
    return SignupModeResponse(mode=mode)


@router.post("/signup-tokens", response_model=CreateSignupTokenResponse)
def create_signup_token(
    payload: CreateSignupTokenRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CreateSignupTokenResponse:
    is_superadmin = current_user.role == Role.SUPERADMIN
    if not is_superadmin and payload.role != Role.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only create user signup tokens",
        )

    if payload.role == Role.USER and not payload.questionnaire_version_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User signup tokens must include questionnaire visibility scope",
        )

    if payload.role != Role.USER and payload.questionnaire_version_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Questionnaire visibility scope is only valid for user signup tokens",
        )

    # TODO: Enforce questionnaire ownership for admin issuers once questionnaire tables are added.
    token_record, token = issue_signup_token(
        db,
        role=payload.role,
        created_by_id=current_user.id,
        expires_in_minutes=payload.expires_in_minutes,
        token_pepper=settings.token_pepper,
        questionnaire_version_ids=payload.questionnaire_version_ids,
    )
    db.commit()
    db.refresh(token_record)

    return CreateSignupTokenResponse(
        token=token,
        token_hint=token_record.token_hint,
        role=token_record.role_to_grant,
        expires_at=token_record.expires_at,
        questionnaire_version_ids=deserialize_visibility_scope(token_record.visibility_scope_json),
    )


@router.get("/signup-tokens", response_model=SignupTokenListResponse)
def list_signup_tokens(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> SignupTokenListResponse:
    stmt = select(SignupToken).order_by(desc(SignupToken.created_at)).limit(100)
    if current_user.role == Role.ADMIN:
        stmt = stmt.where(SignupToken.created_by_id == current_user.id)

    records = db.execute(stmt).scalars().all()
    items = [
        SignupTokenSummary(
            id=record.id,
            token_hint=record.token_hint,
            role_to_grant=record.role_to_grant,
            created_at=record.created_at,
            expires_at=record.expires_at,
            used_at=record.used_at,
            questionnaire_version_ids=deserialize_visibility_scope(record.visibility_scope_json),
        )
        for record in records
    ]
    return SignupTokenListResponse(items=items)
