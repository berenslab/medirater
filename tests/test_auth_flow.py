from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import PasskeyCredential, Role, User, UserAssignment
from app.services.token_service import issue_signup_token


def _signup(
    client: TestClient,
    username: str,
    token: str | None = None,
    *,
    credential_suffix: str = "1",
) -> dict:
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
                "id": f"cred-{username}-{credential_suffix}",
                "response": {"attestationObject": f"pk-{username}-{credential_suffix}"},
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


def _set_public_signup_open(test_session_factory) -> None:
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
        mode = client.put(
            "/api/admin/settings/public-signup-mode",
            json={"mode": "open"},
        )
        assert mode.status_code == 200
        assert mode.json()["mode"] == "open"


def test_default_signup_mode_is_invite_only() -> None:
    with TestClient(app) as client:
        blocked = client.post("/api/auth/signup/begin", json={"username": "alice"})
        assert blocked.status_code == 403


def test_open_signup_and_login_flow(test_session_factory) -> None:
    _set_public_signup_open(test_session_factory)

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


def test_username_is_unique_case_insensitive(test_session_factory) -> None:
    _set_public_signup_open(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "alice")

        duplicate = client.post("/api/auth/signup/begin", json={"username": "ALICE"})
        assert duplicate.status_code == 409


def test_signup_token_can_recover_existing_user_with_new_passkey(test_session_factory) -> None:
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
        version_id = _create_published_questionnaire_version(super_client, "Recovery scope questionnaire")

        signup_token_resp = super_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert signup_token_resp.status_code == 200
        signup_token = signup_token_resp.json()["token"]

        recovery_token_resp = super_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert recovery_token_resp.status_code == 200
        recovery_token = recovery_token_resp.json()["token"]

    with TestClient(app) as first_device_client:
        _signup(first_device_client, "alice", token=signup_token, credential_suffix="1")

    with test_session_factory() as db:
        alice_before = db.scalar(select(User).where(User.username == "alice"))
        assert alice_before is not None
        alice_id_before = alice_before.id

    with TestClient(app) as recovery_client:
        recovery_complete = _signup(
            recovery_client,
            "alice",
            token=recovery_token,
            credential_suffix="2",
        )
        assert recovery_complete["user"]["username"] == "alice"

        reused = recovery_client.post(
            "/api/auth/signup/begin",
            json={"username": "alice", "token": recovery_token},
        )
        assert reused.status_code == 400

    with test_session_factory() as db:
        alice_after = db.scalar(select(User).where(User.username == "alice"))
        assert alice_after is not None
        assert alice_after.id == alice_id_before

        assignments = (
            db.execute(
                select(UserAssignment.questionnaire_version_id)
                .where(UserAssignment.user_id == alice_after.id)
                .order_by(UserAssignment.questionnaire_version_id)
            )
            .scalars()
            .all()
        )
        assert assignments == [version_id]

        credential_ids = (
            db.execute(
                select(PasskeyCredential.credential_id)
                .where(PasskeyCredential.user_id == alice_after.id)
                .order_by(PasskeyCredential.credential_id)
            )
            .scalars()
            .all()
        )
        assert credential_ids == ["cred-alice-1", "cred-alice-2"]


def test_signup_recovery_requires_token_role_to_match_existing_user_role(test_session_factory) -> None:
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
        version_id = _create_published_questionnaire_version(super_client, "Role-match scope questionnaire")

        user_token_resp = super_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert user_token_resp.status_code == 200
        user_token = user_token_resp.json()["token"]

        admin_token_resp = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_token_resp.status_code == 200
        admin_token = admin_token_resp.json()["token"]

    with TestClient(app) as first_device_client:
        _signup(first_device_client, "alice", token=user_token, credential_suffix="1")

    with TestClient(app) as recovery_client:
        blocked = recovery_client.post(
            "/api/auth/signup/begin",
            json={"username": "alice", "token": admin_token},
        )
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "Signup token role does not match existing user role"


def test_account_update_username_uniqueness_and_yoe_requirement(test_session_factory) -> None:
    _set_public_signup_open(test_session_factory)

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        _signup(alice_client, "alice")
        _signup(bob_client, "bob")

        conflict = alice_client.patch("/api/auth/me", json={"username": "bob"})
        assert conflict.status_code == 409

        missing_yoe = alice_client.patch("/api/auth/me", json={"year_of_experience": None})
        assert missing_yoe.status_code == 400

        updated = alice_client.patch(
            "/api/auth/me",
            json={"username": "alice.updated", "year_of_experience": 7},
        )
        assert updated.status_code == 200
        assert updated.json()["username"] == "alice.updated"
        assert updated.json()["year_of_experience"] == 7

        me = alice_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice.updated"
        assert me.json()["year_of_experience"] == 7


def test_invite_only_mode_blocks_public_signup_without_token_and_allows_unscoped_user_invites(
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
        assert token_resp.status_code == 200
        assert token_resp.json()["questionnaire_version_ids"] == []

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

        _signup(client, "bob_unscoped", token=token_resp.json()["token"])
        _signup(client, "bob_scoped", token=scoped_token_resp.json()["token"])

    with test_session_factory() as db:
        bob_unscoped = db.scalar(select(User).where(User.username == "bob_unscoped"))
        assert bob_unscoped is not None
        unscoped_assignments = (
            db.execute(
                select(UserAssignment.questionnaire_version_id).where(
                    UserAssignment.user_id == bob_unscoped.id
                ).order_by(UserAssignment.questionnaire_version_id)
            )
            .scalars()
            .all()
        )
        assert unscoped_assignments == []

        bob_scoped = db.scalar(select(User).where(User.username == "bob_scoped"))
        assert bob_scoped is not None
        scoped_assignments = (
            db.execute(
                select(UserAssignment.questionnaire_version_id).where(
                    UserAssignment.user_id == bob_scoped.id
                ).order_by(UserAssignment.questionnaire_version_id)
            )
            .scalars()
            .all()
        )
        assert scoped_assignments == [version_id]


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
        assert missing_scope.status_code == 200
        assert missing_scope.json()["questionnaire_version_ids"] == []

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


def test_superadmin_admin_token_can_include_scope_and_assigns_on_signup(test_session_factory) -> None:
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
        version_id = _create_published_questionnaire_version(client, "Admin scope target")

        admin_token_resp = client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "admin",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert admin_token_resp.status_code == 200
        assert admin_token_resp.json()["questionnaire_version_ids"] == [version_id]

        created_admin = _signup(client, "designer_scoped", token=admin_token_resp.json()["token"])
        assert created_admin["user"]["role"] == "admin"

    with test_session_factory() as db:
        admin_user = db.scalar(select(User).where(User.username == "designer_scoped"))
        assert admin_user is not None
        assignments = (
            db.execute(
                select(UserAssignment.questionnaire_version_id).where(
                    UserAssignment.user_id == admin_user.id
                )
            )
            .scalars()
            .all()
        )
        assert assignments == [version_id]


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
