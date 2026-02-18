from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_admin_user, get_superadmin_user
from app.models import (
    Questionnaire,
    QuestionnaireVersion,
    QuestionnaireVersionStatus,
    Role,
    SignupToken,
    User,
    UserAssignment,
)
from app.schemas import (
    AssignmentCreateRequest,
    AssignmentOut,
    AssignmentUpdateRequest,
    AdminManagedUserOut,
    AdminUpdateUserRequest,
    CreateSignupTokenRequest,
    CreateSignupTokenResponse,
    SignupModeResponse,
    SignupTokenScopeOption,
    SignupTokenScopeOptionsResponse,
    SignupTokenListResponse,
    SignupTokenSummary,
    UpdateSignupModeRequest,
)
from app.security import normalize_username
from app.services.settings_service import get_public_signup_mode, set_public_signup_mode
from app.services.token_service import (
    deserialize_visibility_scope,
    issue_signup_token,
    normalize_visibility_scope,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _to_admin_managed_user_out(user: User) -> AdminManagedUserOut:
    return AdminManagedUserOut.model_validate(user)


def _to_assignment_out(
    assignment: UserAssignment,
    *,
    user: User,
    version: QuestionnaireVersion,
    questionnaire: Questionnaire,
    granted_by_username: str | None,
) -> AssignmentOut:
    return AssignmentOut(
        id=assignment.id,
        username=user.username,
        user_role=user.role,
        questionnaire_id=questionnaire.id,
        questionnaire_version_id=version.id,
        questionnaire_title=questionnaire.title,
        questionnaire_version_number=version.version_number,
        granted_by_username=granted_by_username,
        is_active=assignment.is_active,
        created_at=assignment.created_at,
    )


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


@router.get("/signup-token-scope-options", response_model=SignupTokenScopeOptionsResponse)
def list_signup_token_scope_options(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> SignupTokenScopeOptionsResponse:
    stmt = (
        select(QuestionnaireVersion, Questionnaire, User.username)
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .join(User, User.id == Questionnaire.owner_admin_id)
        .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
        .order_by(asc(Questionnaire.title), asc(QuestionnaireVersion.version_number))
    )
    if current_user.role == Role.ADMIN:
        stmt = stmt.where(Questionnaire.owner_admin_id == current_user.id)

    rows = db.execute(stmt).all()
    items = [
        SignupTokenScopeOption(
            questionnaire_id=questionnaire.id,
            questionnaire_title=questionnaire.title,
            questionnaire_slug=questionnaire.slug,
            questionnaire_description=questionnaire.description,
            questionnaire_owner_username=owner_username,
            questionnaire_version_id=version.id,
            questionnaire_version_number=version.version_number,
        )
        for version, questionnaire, owner_username in rows
    ]
    return SignupTokenScopeOptionsResponse(items=items)


@router.get("/users", response_model=list[AdminManagedUserOut])
def list_users_for_superadmin(
    _: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
) -> list[AdminManagedUserOut]:
    users = db.execute(select(User).order_by(desc(User.created_at)).limit(500)).scalars().all()
    return [_to_admin_managed_user_out(user) for user in users]


@router.get("/assignment-target-users", response_model=list[AdminManagedUserOut])
def list_users_for_assignment_targets(
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[AdminManagedUserOut]:
    users = (
        db.execute(select(User).where(User.is_active.is_(True)).order_by(asc(User.username)).limit(1000))
        .scalars()
        .all()
    )
    return [_to_admin_managed_user_out(user) for user in users]


@router.patch("/users/{username}", response_model=AdminManagedUserOut)
def update_user_for_superadmin(
    username: str,
    payload: AdminUpdateUserRequest,
    current_user: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
) -> AdminManagedUserOut:
    if payload.role is None and payload.is_active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (role or is_active) must be provided",
        )

    normalized_username = normalize_username(username)
    user = db.scalar(select(User).where(User.username == normalized_username))
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


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
    target_username: str | None = Query(default=None),
    questionnaire_version_id: str | None = Query(default=None),
    questionnaire_id: str | None = Query(default=None),
) -> list[AssignmentOut]:
    stmt = (
        select(UserAssignment, User, QuestionnaireVersion, Questionnaire)
        .join(User, User.id == UserAssignment.user_id)
        .join(QuestionnaireVersion, QuestionnaireVersion.id == UserAssignment.questionnaire_version_id)
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .order_by(desc(UserAssignment.created_at))
        .limit(1000)
    )
    if target_username:
        stmt = stmt.where(User.username == normalize_username(target_username))
    if questionnaire_version_id:
        stmt = stmt.where(UserAssignment.questionnaire_version_id == questionnaire_version_id)
    if questionnaire_id:
        stmt = stmt.where(Questionnaire.id == questionnaire_id)
    if current_user.role == Role.ADMIN:
        stmt = stmt.where(Questionnaire.owner_admin_id == current_user.id)

    rows = db.execute(stmt).all()
    granted_by_ids = {assignment.granted_by_id for assignment, _, _, _ in rows if assignment.granted_by_id}
    granted_by_username_by_id: dict[str, str] = {}
    if granted_by_ids:
        granted_by_rows = db.execute(
            select(User.id, User.username).where(User.id.in_(granted_by_ids))
        ).all()
        granted_by_username_by_id = {user_id: username for user_id, username in granted_by_rows}

    return [
        _to_assignment_out(
            assignment,
            user=user,
            version=version,
            questionnaire=questionnaire,
            granted_by_username=granted_by_username_by_id.get(assignment.granted_by_id or ""),
        )
        for assignment, user, version, questionnaire in rows
    ]


@router.post("/assignments", response_model=AssignmentOut)
def create_or_update_assignment(
    payload: AssignmentCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> AssignmentOut:
    target_user = db.scalar(
        select(User).where(User.username == normalize_username(payload.target_username))
    )
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    version_row = db.execute(
        select(QuestionnaireVersion, Questionnaire)
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .where(QuestionnaireVersion.id == payload.questionnaire_version_id)
    ).first()
    if not version_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire version not found")
    version, questionnaire = version_row

    if version.status != QuestionnaireVersionStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published questionnaire versions can be assigned",
        )

    if current_user.role == Role.ADMIN and questionnaire.owner_admin_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only assign versions from their own questionnaires",
        )

    assignment = db.scalar(
        select(UserAssignment)
        .where(UserAssignment.user_id == target_user.id)
        .where(UserAssignment.questionnaire_version_id == version.id)
    )
    if not assignment:
        assignment = UserAssignment(
            user_id=target_user.id,
            questionnaire_version_id=version.id,
            granted_by_id=current_user.id,
            is_active=payload.is_active,
        )
        db.add(assignment)
    else:
        assignment.is_active = payload.is_active
        assignment.granted_by_id = current_user.id

    db.commit()
    db.refresh(assignment)
    return _to_assignment_out(
        assignment,
        user=target_user,
        version=version,
        questionnaire=questionnaire,
        granted_by_username=current_user.username,
    )


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
def update_assignment(
    assignment_id: str,
    payload: AssignmentUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> AssignmentOut:
    row = db.execute(
        select(UserAssignment, User, QuestionnaireVersion, Questionnaire)
        .join(User, User.id == UserAssignment.user_id)
        .join(QuestionnaireVersion, QuestionnaireVersion.id == UserAssignment.questionnaire_version_id)
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .where(UserAssignment.id == assignment_id)
    ).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    assignment, target_user, version, questionnaire = row

    if current_user.role == Role.ADMIN and questionnaire.owner_admin_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only manage assignments for their own questionnaires",
        )

    assignment.is_active = payload.is_active
    assignment.granted_by_id = current_user.id
    db.commit()
    db.refresh(assignment)
    return _to_assignment_out(
        assignment,
        user=target_user,
        version=version,
        questionnaire=questionnaire,
        granted_by_username=current_user.username,
    )
