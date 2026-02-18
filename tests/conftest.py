from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.services.settings_service import ensure_default_settings


@pytest.fixture(scope="session")
def test_engine(tmp_path_factory: pytest.TempPathFactory):
    db_dir = tmp_path_factory.mktemp("db")
    db_path = db_dir / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def isolated_test_db(test_engine, test_session_factory) -> Generator[None, None, None]:
    settings = get_settings()
    previous_insecure = settings.insecure_dev_webauthn
    settings.insecure_dev_webauthn = True

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    with test_session_factory() as db:
        ensure_default_settings(db)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield
    finally:
        app.dependency_overrides.clear()
        settings.insecure_dev_webauthn = previous_insecure
