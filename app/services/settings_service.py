import json

from sqlalchemy.orm import Session

from app.models import AppSetting, AuditLog, SignupMode, utcnow

PUBLIC_SIGNUP_MODE_KEY = "public_user_signup_mode"


def ensure_default_settings(db: Session) -> None:
    existing = db.get(AppSetting, PUBLIC_SIGNUP_MODE_KEY)
    if existing:
        return
    db.add(AppSetting(key=PUBLIC_SIGNUP_MODE_KEY, value=SignupMode.OPEN.value))
    db.commit()


def get_public_signup_mode(db: Session) -> SignupMode:
    setting = db.get(AppSetting, PUBLIC_SIGNUP_MODE_KEY)
    if not setting:
        return SignupMode.OPEN
    return SignupMode(setting.value)


def set_public_signup_mode(db: Session, *, mode: SignupMode, actor_user_id: str) -> SignupMode:
    current = db.get(AppSetting, PUBLIC_SIGNUP_MODE_KEY)
    if not current:
        current = AppSetting(key=PUBLIC_SIGNUP_MODE_KEY, value=mode.value, updated_by_id=actor_user_id)
        db.add(current)
        old_value = None
    else:
        old_value = current.value
        current.value = mode.value
        current.updated_by_id = actor_user_id
        current.updated_at = utcnow()

    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action="public_signup_mode_changed",
            details_json=json.dumps(
                {
                    "old_value": old_value,
                    "new_value": mode.value,
                    "setting_key": PUBLIC_SIGNUP_MODE_KEY,
                }
            ),
        )
    )
    db.commit()
    db.refresh(current)
    return SignupMode(current.value)
