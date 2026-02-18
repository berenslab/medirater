from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    PasskeyCredential,
    PendingChallenge,
    Role,
    SignupMode,
    SignupToken,
    User,
    UserAssignment,
    utcnow,
)
from app.schemas import (
    AuthSessionResponse,
    LoginBeginRequest,
    LoginCompleteRequest,
    SignupModeResponse,
    SignupBeginRequest,
    SignupCompleteRequest,
    UserOut,
    WebAuthnBeginResponse,
)
from app.security import normalize_username, validate_username
from app.services.session_service import create_session, delete_session
from app.services.settings_service import get_public_signup_mode
from app.services.token_service import (
    consume_signup_token,
    deserialize_visibility_scope,
    get_valid_signup_token,
)
from app.services.webauthn import (
    extract_credential_id,
    generate_authentication_public_key_options,
    generate_registration_public_key_options,
    verify_authentication_response,
    verify_registration_response,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/public-signup-mode", response_model=SignupModeResponse)
def public_signup_mode(db: Session = Depends(get_db)) -> SignupModeResponse:
    return SignupModeResponse(mode=get_public_signup_mode(db))


@router.post("/signup/begin", response_model=WebAuthnBeginResponse)
def signup_begin(
    payload: SignupBeginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebAuthnBeginResponse:
    username = normalize_username(payload.username)
    try:
        validate_username(username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    existing_user = db.scalar(select(User).where(User.username == username))
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken")

    token_record: SignupToken | None = None
    if payload.token:
        token_record = get_valid_signup_token(db, token=payload.token, token_pepper=settings.token_pepper)
        if not token_record:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired signup token")
    else:
        mode = get_public_signup_mode(db)
        if mode == SignupMode.INVITE_ONLY:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signup token is required")

    try:
        public_key_options, challenge = generate_registration_public_key_options(
            rp_id=settings.webauthn_rp_id,
            rp_name=settings.webauthn_rp_name,
            username=username,
            user_id=username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    pending = PendingChallenge(
        flow="signup",
        challenge=challenge,
        username=username,
        token_id=token_record.id if token_record else None,
        expires_at=utcnow() + timedelta(minutes=settings.challenge_ttl_minutes),
    )
    db.add(pending)
    db.commit()

    return WebAuthnBeginResponse(
        challenge_id=pending.id,
        public_key=public_key_options,
    )


@router.post("/signup/complete", response_model=AuthSessionResponse)
def signup_complete(
    payload: SignupCompleteRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    pending = db.scalar(
        select(PendingChallenge)
        .where(PendingChallenge.id == payload.challenge_id)
        .where(PendingChallenge.flow == "signup")
    )
    if not pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid challenge")
    if pending.expires_at <= utcnow():
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge expired")
    if not pending.username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid challenge state")

    try:
        registration = verify_registration_response(
            challenge=pending.challenge,
            credential=payload.credential,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            insecure_dev_webauthn=settings.insecure_dev_webauthn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    existing_user = db.scalar(select(User).where(User.username == pending.username))
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken")

    existing_credential = db.scalar(
        select(PasskeyCredential).where(PasskeyCredential.credential_id == registration.credential_id)
    )
    if existing_credential:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Credential already registered")

    role = Role.USER
    token_record: SignupToken | None = None
    if pending.token_id:
        token_record = db.get(SignupToken, pending.token_id)
        if not token_record or token_record.used_at is not None or token_record.expires_at <= utcnow():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signup token no longer valid")
        role = token_record.role_to_grant
    else:
        mode = get_public_signup_mode(db)
        if mode == SignupMode.INVITE_ONLY:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signup token is required")

    user = User(username=pending.username, role=role)
    db.add(user)
    db.flush()

    db.add(
        PasskeyCredential(
            user_id=user.id,
            credential_id=registration.credential_id,
            public_key=registration.public_key,
            sign_count=registration.sign_count,
            label="Primary passkey",
        )
    )

    if token_record:
        if token_record.role_to_grant in {Role.USER, Role.ADMIN}:
            for questionnaire_version_id in deserialize_visibility_scope(token_record.visibility_scope_json):
                db.add(
                    UserAssignment(
                        user_id=user.id,
                        questionnaire_version_id=questionnaire_version_id,
                        granted_by_id=token_record.created_by_id,
                    )
                )
        consume_signup_token(token_record, used_by_id=user.id)

    session_token = create_session(db, user_id=user.id, ttl_hours=settings.session_ttl_hours)
    db.delete(pending)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Signup failed due to concurrent update") from exc

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token.id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
    )

    return AuthSessionResponse(user=UserOut.model_validate(user), session_expires_at=session_token.expires_at)


@router.post("/login/begin", response_model=WebAuthnBeginResponse)
def login_begin(
    payload: LoginBeginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebAuthnBeginResponse:
    username = normalize_username(payload.username)
    try:
        validate_username(username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = db.scalar(select(User).where(User.username == username).where(User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    credential_ids = [
        row[0]
        for row in db.execute(
            select(PasskeyCredential.credential_id).where(PasskeyCredential.user_id == user.id)
        ).all()
    ]
    if not credential_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No passkeys registered")

    try:
        public_key_options, challenge = generate_authentication_public_key_options(
            rp_id=settings.webauthn_rp_id,
            allow_credential_ids=credential_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    pending = PendingChallenge(
        flow="login",
        challenge=challenge,
        user_id=user.id,
        expires_at=utcnow() + timedelta(minutes=settings.challenge_ttl_minutes),
    )
    db.add(pending)
    db.commit()

    return WebAuthnBeginResponse(
        challenge_id=pending.id,
        public_key=public_key_options,
    )


@router.post("/login/complete", response_model=AuthSessionResponse)
def login_complete(
    payload: LoginCompleteRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSessionResponse:
    pending = db.scalar(
        select(PendingChallenge)
        .where(PendingChallenge.id == payload.challenge_id)
        .where(PendingChallenge.flow == "login")
    )
    if not pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid challenge")
    if pending.expires_at <= utcnow() or not pending.user_id:
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge expired")

    try:
        credential_id = extract_credential_id(payload.credential)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    credential = db.scalar(
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == pending.user_id)
        .where(PasskeyCredential.credential_id == credential_id)
    )
    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credential does not match user")

    try:
        authn = verify_authentication_response(
            challenge=pending.challenge,
            credential=payload.credential,
            credential_public_key=credential.public_key,
            current_sign_count=credential.sign_count,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            insecure_dev_webauthn=settings.insecure_dev_webauthn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = db.get(User, pending.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    credential.sign_count = authn.new_sign_count
    credential.last_used_at = utcnow()

    session_token = create_session(db, user_id=user.id, ttl_hours=settings.session_ttl_hours)
    db.delete(pending)
    db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token.id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
    )

    return AuthSessionResponse(user=UserOut.model_validate(user), session_expires_at=session_token.expires_at)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    session_token = request.cookies.get(settings.session_cookie_name)
    if session_token:
        delete_session(db, session_token=session_token)

    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
