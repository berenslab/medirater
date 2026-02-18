from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import Role, User, UserAssignment
from app.services.token_service import issue_signup_token


def _signup(client: TestClient, username: str, token: str | None = None) -> dict:
    payload = {"username": username}
    if token:
        payload["token"] = token

    begin = client.post("/api/auth/signup/begin", json=payload)
    assert begin.status_code == 200

    challenge_id = begin.json()["challenge_id"]
    complete = client.post(
        "/api/auth/signup/complete",
        json={
            "challenge_id": challenge_id,
            "credential": {
                "id": f"cred-{username}-1",
                "response": {"attestationObject": f"pk-{username}-1"},
            },
        },
    )
    assert complete.status_code == 200
    return complete.json()


def _create_published_questionnaire_version(client: TestClient, title: str) -> str:
    created = client.post(
        "/api/admin/questionnaires",
        json={"title": title, "description": "desc", "instructions_markdown": "hello"},
    )
    assert created.status_code == 200
    questionnaire = created.json()
    questionnaire_id = questionnaire["id"]
    version_id = questionnaire["latest_version_id"]
    assert version_id is not None

    published = client.post(
        f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
    )
    assert published.status_code == 200
    return version_id


def test_open_signup_and_login_flow() -> None:
    with TestClient(app) as client:
        signup = _signup(client, "alice")
        assert signup["user"]["role"] == "user"

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice"

        login_begin = client.post("/api/auth/login/begin", json={"username": "alice"})
        assert login_begin.status_code == 200

        login_challenge_id = login_begin.json()["challenge_id"]
        login_complete = client.post(
            "/api/auth/login/complete",
            json={"challenge_id": login_challenge_id, "credential": {"id": "cred-alice-1"}},
        )
        assert login_complete.status_code == 200


def test_username_is_unique_case_insensitive() -> None:
    with TestClient(app) as client:
        _signup(client, "alice")

        duplicate = client.post("/api/auth/signup/begin", json={"username": "ALICE"})
        assert duplicate.status_code == 409


def test_invite_only_mode_blocks_public_signup_without_token_and_user_invite_requires_scope(
    test_session_factory,
) -> None:
    settings = get_settings()

    with test_session_factory() as db:
        _, bootstrap_token = issue_signup_token(
            db,
            role=Role.SUPERADMIN,
            created_by_id=None,
            expires_in_minutes=60,
            token_pepper=settings.token_pepper,
        )
        db.commit()

    with TestClient(app) as client:
        complete_super = _signup(client, "root", token=bootstrap_token)
        assert complete_super["user"]["role"] == "superadmin"

        mode = client.put(
            "/api/admin/settings/public-signup-mode",
            json={"mode": "invite_only"},
        )
        assert mode.status_code == 200

        blocked = client.post("/api/auth/signup/begin", json={"username": "bob"})
        assert blocked.status_code == 403

        token_resp = client.post(
            "/api/admin/signup-tokens",
            json={"role": "user", "expires_in_minutes": 60},
        )
        assert token_resp.status_code == 400

        version_id = _create_published_questionnaire_version(client, "Initial published questionnaire")

        scoped_token_resp = client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert scoped_token_resp.status_code == 200
        assert scoped_token_resp.json()["questionnaire_version_ids"] == [version_id]

        _signup(client, "bob", token=scoped_token_resp.json()["token"])

    with test_session_factory() as db:
        bob = db.scalar(select(User).where(User.username == "bob"))
        assert bob is not None
        assignments = (
            db.execute(
                select(UserAssignment.questionnaire_version_id).where(
                    UserAssignment.user_id == bob.id
                ).order_by(UserAssignment.questionnaire_version_id)
            )
            .scalars()
            .all()
        )
        assert assignments == [version_id]


def test_admin_can_only_create_user_tokens(test_session_factory) -> None:
    settings = get_settings()

    with test_session_factory() as db:
        _, bootstrap_token = issue_signup_token(
            db,
            role=Role.SUPERADMIN,
            created_by_id=None,
            expires_in_minutes=60,
            token_pepper=settings.token_pepper,
        )
        db.commit()

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        admin_token_resp = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_token_resp.status_code == 200

        _signup(client, "designer", token=admin_token_resp.json()["token"])

        forbidden = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert forbidden.status_code == 403

        version_id = _create_published_questionnaire_version(client, "Admin-owned questionnaire")

        missing_scope = client.post(
            "/api/admin/signup-tokens",
            json={"role": "user", "expires_in_minutes": 60},
        )
        assert missing_scope.status_code == 400

        ok = client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert ok.status_code == 200
        assert ok.json()["questionnaire_version_ids"] == [version_id]


def test_superadmin_user_management_endpoints(test_session_factory) -> None:
    settings = get_settings()

    with test_session_factory() as db:
        _, bootstrap_token = issue_signup_token(
            db,
            role=Role.SUPERADMIN,
            created_by_id=None,
            expires_in_minutes=60,
            token_pepper=settings.token_pepper,
        )
        db.commit()

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)

        admin_token = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_token.status_code == 200

        with TestClient(app) as admin_client:
            _signup(admin_client, "designer", token=admin_token.json()["token"])

            forbidden_list = admin_client.get("/api/admin/users")
            assert forbidden_list.status_code == 403

        users = super_client.get("/api/admin/users")
        assert users.status_code == 200
        usernames = {item["username"] for item in users.json()}
        assert "root" in usernames
        assert "designer" in usernames

        updated_admin = super_client.patch(
            "/api/admin/users/designer",
            json={"role": "user", "is_active": True},
        )
        assert updated_admin.status_code == 200
        assert updated_admin.json()["role"] == "user"

        forbid_self_deactivate = super_client.patch(
            "/api/admin/users/root",
            json={"is_active": False},
        )
        assert forbid_self_deactivate.status_code == 400
