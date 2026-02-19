from sqlalchemy import create_engine

from app.services.schema_service import ensure_runtime_schema


def test_runtime_schema_adds_version_consent_text_and_backfills(tmp_path) -> None:
    db_path = tmp_path / "runtime-schema.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE questionnaires (
                id VARCHAR(36) PRIMARY KEY,
                title VARCHAR(200),
                slug VARCHAR(220),
                consent_text TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE questionnaire_versions (
                id VARCHAR(36) PRIMARY KEY,
                questionnaire_id VARCHAR(36),
                version_number INTEGER
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE users (
                id VARCHAR(36) PRIMARY KEY,
                username VARCHAR(64)
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO questionnaires (id, title, slug, consent_text)
            VALUES ('q1', 'OCT Reader', 'oct-reader', 'Legacy consent text')
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO questionnaire_versions (id, questionnaire_id, version_number)
            VALUES ('v1', 'q1', 1)
            """
        )

    ensure_runtime_schema(engine)

    with engine.begin() as conn:
        version_columns = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(questionnaire_versions)").fetchall()
        }
        assert "consent_text" in version_columns

        row = conn.exec_driver_sql(
            "SELECT consent_text FROM questionnaire_versions WHERE id = 'v1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Legacy consent text"

        user_columns = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()
        }
        assert "year_of_experience" in user_columns
