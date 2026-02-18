#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Role
from app.services.token_service import issue_signup_token


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create one-time bootstrap superadmin signup token")
    parser.add_argument("--expires-in-minutes", type=int, default=60, help="Token validity duration")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        token_record, token = issue_signup_token(
            db,
            role=Role.SUPERADMIN,
            created_by_id=None,
            expires_in_minutes=args.expires_in_minutes,
            token_pepper=settings.token_pepper,
        )
        db.commit()
        print("Bootstrap superadmin token (single-use):")
        print(token)
        print(f"hint={token_record.token_hint} expires_at={token_record.expires_at.isoformat()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
