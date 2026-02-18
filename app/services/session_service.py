from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SessionToken, User, utcnow
from app.security import generate_session_token


def create_session(db: Session, *, user_id: str, ttl_hours: int) -> SessionToken:
    token = SessionToken(
        id=generate_session_token(),
        user_id=user_id,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(token)
    db.flush()
    return token


def delete_session(db: Session, *, session_token: str) -> None:
    token = db.get(SessionToken, session_token)
    if token:
        db.delete(token)
        db.commit()


def get_user_by_session_token(db: Session, *, session_token: str) -> User | None:
    stmt = (
        select(User)
        .join(SessionToken, SessionToken.user_id == User.id)
        .where(SessionToken.id == session_token)
        .where(SessionToken.expires_at > utcnow())
        .where(User.is_active.is_(True))
    )
    return db.scalar(stmt)
