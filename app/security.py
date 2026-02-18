import base64
import hashlib
import re
import secrets
import uuid

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def normalize_username(raw_username: str) -> str:
    return raw_username.strip().lower()


def validate_username(username: str) -> None:
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Username must match [a-z0-9_.-] and be 3-32 chars long")


def generate_webauthn_challenge() -> str:
    return _b64url(secrets.token_bytes(32))


def generate_session_token() -> str:
    return _b64url(secrets.token_bytes(32))


def generate_signup_token() -> str:
    return str(uuid.uuid4())


def hash_signup_token(token: str, pepper: str) -> str:
    digest = hashlib.sha256()
    digest.update(token.encode("utf-8"))
    digest.update(b".")
    digest.update(pepper.encode("utf-8"))
    return digest.hexdigest()
