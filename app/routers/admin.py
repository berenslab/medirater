from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_admin_user, get_superadmin_user
from app.models import Questionnaire, QuestionnaireVersion, QuestionnaireVersionStatus, Role, SignupToken, User
from app.schemas import (
    AdminManagedUserOut,
    AdminUpdateUserRequest,
    CreateSignupTokenRequest,
    CreateSignupTokenResponse,
    SignupModeResponse,
    SignupTokenListResponse,
    SignupTokenSummary,
    UpdateSignupModeRequest,
)
from app.services.settings_service import get_public_signup_mode, set_public_signup_mode
from app.services.token_service import (
    deserialize_visibility_scope,
    issue_signup_token,
    normalize_visibility_scope,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _to_admin_managed_user_out(user: User) -> AdminManagedUserOut:
    return AdminManagedUserOut.model_validate(user)


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
    normalized_scope = normalize_visibility_scope(payload.questionnaire_version_ids)

    if not is_superadmin and payload.role != Role.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only create user signup tokens",
        )

    if payload.role == Role.USER and not normalized_scope:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User signup tokens must include questionnaire visibility scope",
        )

    if payload.role != Role.USER and normalized_scope:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Questionnaire visibility scope is only valid for user signup tokens",
        )

    if payload.role == Role.USER:
        rows = db.execute(
            select(QuestionnaireVersion, Questionnaire.owner_admin_id)
            .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
            .where(QuestionnaireVersion.id.in_(normalized_scope))
        ).all()
        versions_by_id = {version.id: (version, owner_admin_id) for version, owner_admin_id in rows}

        missing_ids = [item for item in normalized_scope if item not in versions_by_id]
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown questionnaire version ids: {', '.join(missing_ids)}",
            )

        non_published = [
            item
            for item in normalized_scope
            if versions_by_id[item][0].status != QuestionnaireVersionStatus.PUBLISHED
        ]
        if non_published:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only published questionnaire versions can be assigned: {', '.join(non_published)}",
            )

        if not is_superadmin:
            forbidden_ids = [
                item for item in normalized_scope if versions_by_id[item][1] != current_user.id
            ]
            if forbidden_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Admins can only assign questionnaire versions from their own questionnaires. "
                        f"Forbidden ids: {', '.join(forbidden_ids)}"
                    ),
                )

    token_record, token = issue_signup_token(
        db,
        role=payload.role,
        created_by_id=current_user.id,
        expires_in_minutes=payload.expires_in_minutes,
        token_pepper=settings.token_pepper,
        questionnaire_version_ids=normalized_scope,
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


@router.get("/users", response_model=list[AdminManagedUserOut])
def list_users_for_superadmin(
    _: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
) -> list[AdminManagedUserOut]:
    users = db.execute(select(User).order_by(desc(User.created_at)).limit(500)).scalars().all()
    return [_to_admin_managed_user_out(user) for user in users]


@router.patch("/users/{user_id}", response_model=AdminManagedUserOut)
def update_user_for_superadmin(
    user_id: str,
    payload: AdminUpdateUserRequest,
    current_user: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
) -> AdminManagedUserOut:
    if payload.role is None and payload.is_active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (role or is_active) must be provided",
        )

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id:
        if payload.role and payload.role != Role.SUPERADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role from superadmin",
            )
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own account",
            )

    next_role = payload.role if payload.role is not None else user.role
    next_is_active = payload.is_active if payload.is_active is not None else user.is_active
    if user.role == Role.SUPERADMIN and (
        next_role != Role.SUPERADMIN or not next_is_active
    ):
        superadmin_count = db.scalar(
            select(func.count(User.id)).where(User.role == Role.SUPERADMIN).where(User.is_active.is_(True))
        )
        if (superadmin_count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove or deactivate the last active superadmin",
            )

    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return _to_admin_managed_user_out(user)
