from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import PasskeyCredential, PendingChallenge, User, utcnow
from app.schemas import (
    BeginPasskeyAddRequest,
    CompletePasskeyAddRequest,
    PasskeyOut,
    PasskeyRenameRequest,
    WebAuthnBeginResponse,
)
from app.services.webauthn import generate_registration_public_key_options, verify_registration_response

router = APIRouter(prefix="/api/passkeys", tags=["passkeys"])


@router.get("", response_model=list[PasskeyOut])
def list_passkeys(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PasskeyOut]:
    records = (
        db.execute(select(PasskeyCredential).where(PasskeyCredential.user_id == current_user.id))
        .scalars()
        .all()
    )
    return [PasskeyOut.model_validate(item) for item in records]


@router.post("/begin-add", response_model=WebAuthnBeginResponse)
def begin_add_passkey(
    _: BeginPasskeyAddRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebAuthnBeginResponse:
    existing_credential_ids = [
        row[0]
        for row in db.execute(
            select(PasskeyCredential.credential_id).where(PasskeyCredential.user_id == current_user.id)
        ).all()
    ]
    try:
        public_key_options, challenge = generate_registration_public_key_options(
            rp_id=settings.webauthn_rp_id,
            rp_name=settings.webauthn_rp_name,
            username=current_user.username,
            user_id=current_user.id,
            exclude_credential_ids=existing_credential_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    pending = PendingChallenge(
        flow="add_passkey",
        challenge=challenge,
        user_id=current_user.id,
        expires_at=utcnow() + timedelta(minutes=settings.challenge_ttl_minutes),
    )
    db.add(pending)
    db.commit()

    return WebAuthnBeginResponse(
        challenge_id=pending.id,
        public_key=public_key_options,
    )


@router.post("/complete-add", response_model=PasskeyOut)
def complete_add_passkey(
    payload: CompletePasskeyAddRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasskeyOut:
    pending = db.scalar(
        select(PendingChallenge)
        .where(PendingChallenge.id == payload.challenge_id)
        .where(PendingChallenge.flow == "add_passkey")
        .where(PendingChallenge.user_id == current_user.id)
    )
    if not pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid challenge")
    if pending.expires_at <= utcnow():
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge expired")

    try:
        result = verify_registration_response(
            challenge=pending.challenge,
            credential=payload.credential,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            insecure_dev_webauthn=settings.insecure_dev_webauthn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    duplicate = db.scalar(select(PasskeyCredential).where(PasskeyCredential.credential_id == result.credential_id))
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Credential already exists")

    passkey = PasskeyCredential(
        user_id=current_user.id,
        credential_id=result.credential_id,
        public_key=result.public_key,
        sign_count=result.sign_count,
        label=payload.label or "Additional passkey",
    )
    db.add(passkey)
    db.delete(pending)
    db.commit()
    db.refresh(passkey)

    return PasskeyOut.model_validate(passkey)


@router.patch("/{passkey_id}", response_model=PasskeyOut)
def rename_passkey(
    passkey_id: str,
    payload: PasskeyRenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PasskeyOut:
    passkey = db.scalar(
        select(PasskeyCredential)
        .where(PasskeyCredential.id == passkey_id)
        .where(PasskeyCredential.user_id == current_user.id)
    )
    if not passkey:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")

    passkey.label = payload.label.strip()
    passkey.updated_at = utcnow()
    db.commit()
    db.refresh(passkey)
    return PasskeyOut.model_validate(passkey)


@router.delete("/{passkey_id}")
def delete_passkey(
    passkey_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    passkey = db.scalar(
        select(PasskeyCredential)
        .where(PasskeyCredential.id == passkey_id)
        .where(PasskeyCredential.user_id == current_user.id)
    )
    if not passkey:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")

    count_stmt = select(func.count(PasskeyCredential.id)).where(PasskeyCredential.user_id == current_user.id)
    credential_count = db.scalar(count_stmt) or 0
    if credential_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the last remaining passkey",
        )

    db.delete(passkey)
    db.commit()
    return {"ok": True}
