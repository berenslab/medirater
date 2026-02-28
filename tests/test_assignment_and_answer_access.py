from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Role
from app.services.token_service import issue_signup_token


def _signup(client: TestClient, username: str, token: str | None = None) -> dict:
    payload = {"username": username}
    if token:
        payload["token"] = token

    begin = client.post("/api/auth/signup/begin", json=payload)
    assert begin.status_code == 200

    complete = client.post(
        "/api/auth/signup/complete",
        json={
            "challenge_id": begin.json()["challenge_id"],
            "credential": {
                "id": f"cred-{username}-1",
                "response": {"attestationObject": f"pk-{username}-1"},
            },
        },
    )
    assert complete.status_code == 200
    return complete.json()


def _login(client: TestClient, username: str) -> dict:
    begin = client.post("/api/auth/login/begin", json={"username": username})
    assert begin.status_code == 200

    complete = client.post(
        "/api/auth/login/complete",
        json={
            "challenge_id": begin.json()["challenge_id"],
            "credential": {"id": f"cred-{username}-1"},
        },
    )
    assert complete.status_code == 200
    return complete.json()


def _bootstrap_superadmin_token(test_session_factory) -> str:
    settings = get_settings()
    with test_session_factory() as db:
        _, token = issue_signup_token(
            db,
            role=Role.SUPERADMIN,
            created_by_id=None,
            expires_in_minutes=60,
            token_pepper=settings.token_pepper,
        )
        db.commit()
        return token


def _create_published_version(client: TestClient, title: str, owner_hint: str) -> tuple[str, str]:
    created = client.post(
        "/api/admin/questionnaires",
        json={"title": title, "description": None, "instructions_markdown": "instructions"},
    )
    assert created.status_code == 200
    questionnaire_id = created.json()["id"]
    version_id = created.json()["latest_version_id"]
    assert version_id

    question = client.post(
        f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
        json={
            "position": 1,
            "prompt_text": f"{owner_hint}: free text answer",
            "question_type": "short_text",
            "is_required": True,
            "config": {},
            "choices": [],
        },
    )
    assert question.status_code == 200

    published = client.post(
        f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
    )
    assert published.status_code == 200
    return questionnaire_id, version_id


def test_assignment_permissions_and_answer_access(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)
        mode = super_client.put(
            "/api/admin/settings/public-signup-mode",
            json={"mode": "open"},
        )
        assert mode.status_code == 200

        admin_a_token = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_a_token.status_code == 200

        admin_b_token = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_b_token.status_code == 200

        user_token = super_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [],
            },
        )
        assert user_token.status_code == 200
        assert user_token.json()["questionnaire_version_ids"] == []

        # Create open user and later assign directly.

    with TestClient(app) as admin_a_client:
        _signup(admin_a_client, "admin_a", token=admin_a_token.json()["token"])
        _questionnaire_a_id, version_a = _create_published_version(
            admin_a_client, "A Questionnaire", "admin_a"
        )

    with TestClient(app) as admin_b_client:
        _signup(admin_b_client, "admin_b", token=admin_b_token.json()["token"])
        questionnaire_b_id, version_b = _create_published_version(
            admin_b_client, "B Questionnaire", "admin_b"
        )

    with TestClient(app) as user_client:
        _signup(user_client, "alice")

    with TestClient(app) as admin_a_client:
        _login(admin_a_client, "admin_a")

        assign_admin_b_to_a = admin_a_client.post(
            "/api/admin/assignments",
            json={
                "target_username": "admin_b",
                "questionnaire_version_id": version_a,
                "is_active": True,
            },
        )
        assert assign_admin_b_to_a.status_code == 200

        forbidden_assign_foreign = admin_a_client.post(
            "/api/admin/assignments",
            json={
                "target_username": "alice",
                "questionnaire_version_id": version_b,
                "is_active": True,
            },
        )
        assert forbidden_assign_foreign.status_code == 403

    with TestClient(app) as super_client:
        _login(super_client, "root")

        assign_admin_a_to_b = super_client.post(
            "/api/admin/assignments",
            json={
                "target_username": "admin_a",
                "questionnaire_version_id": version_b,
                "is_active": True,
            },
        )
        assert assign_admin_a_to_b.status_code == 200

        assign_user_to_b = super_client.post(
            "/api/admin/assignments",
            json={
                "target_username": "alice",
                "questionnaire_version_id": version_b,
                "is_active": True,
            },
        )
        assert assign_user_to_b.status_code == 200

        assignments = super_client.get("/api/admin/assignments")
        assert assignments.status_code == 200
        assert len(assignments.json()) >= 3

        filtered_assignments = super_client.get(
            f"/api/admin/assignments?questionnaire_id={questionnaire_b_id}"
        )
        assert filtered_assignments.status_code == 200
        assert all(
            item["questionnaire_id"] == questionnaire_b_id for item in filtered_assignments.json()
        )

    with TestClient(app) as admin_a_client:
        _login(admin_a_client, "admin_a")

        assignment_targets = admin_a_client.get("/api/admin/assignment-target-users")
        assert assignment_targets.status_code == 200
        usernames = {item["username"] for item in assignment_targets.json()}
        assert "alice" in usernames
        assert "admin_b" in usernames

        own_access = admin_a_client.get(f"/api/user/questionnaires/{version_a}")
        assert own_access.status_code == 200

        assigned_access = admin_a_client.get(f"/api/user/questionnaires/{version_b}")
        assert assigned_access.status_code == 200

    with TestClient(app) as admin_b_client:
        _login(admin_b_client, "admin_b")

        assigned_access = admin_b_client.get(f"/api/user/questionnaires/{version_a}")
        assert assigned_access.status_code == 200

    with TestClient(app) as user_client:
        _login(user_client, "alice")

        assigned_access = user_client.get(f"/api/user/questionnaires/{version_b}")
        assert assigned_access.status_code == 200

        forbidden_unassigned = user_client.get(f"/api/user/questionnaires/{version_a}")
        assert forbidden_unassigned.status_code == 404


def test_bulk_assignment_apply_updates_scope_for_version(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)
        mode = super_client.put(
            "/api/admin/settings/public-signup-mode",
            json={"mode": "open"},
        )
        assert mode.status_code == 200
        _questionnaire_id, version_id = _create_published_version(
            super_client,
            "Bulk assignment target",
            "root",
        )

    with TestClient(app) as alice_client:
        _signup(alice_client, "alice")

    with TestClient(app) as bob_client:
        _signup(bob_client, "bob")

    with TestClient(app) as super_client:
        _login(super_client, "root")

        first_apply = super_client.put(
            "/api/admin/assignments/bulk",
            json={
                "questionnaire_version_id": version_id,
                "active_usernames": ["alice"],
                "scope_usernames": ["alice", "bob"],
            },
        )
        assert first_apply.status_code == 200
        first_json = first_apply.json()
        assert first_json["created_count"] == 1
        assert first_json["updated_count"] == 0
        assert first_json["deactivated_count"] == 0
        assert first_json["unchanged_count"] == 1
        assert first_json["active_usernames"] == ["alice"]

        listed_after_first = super_client.get(
            f"/api/admin/assignments?questionnaire_version_id={version_id}"
        )
        assert listed_after_first.status_code == 200
        listed_first_json = listed_after_first.json()
        assert len(listed_first_json) == 1
        assert listed_first_json[0]["username"] == "alice"
        assert listed_first_json[0]["is_active"] is True

        second_apply = super_client.put(
            "/api/admin/assignments/bulk",
            json={
                "questionnaire_version_id": version_id,
                "active_usernames": ["bob"],
                "scope_usernames": ["alice", "bob"],
            },
        )
        assert second_apply.status_code == 200
        second_json = second_apply.json()
        assert second_json["created_count"] == 1
        assert second_json["updated_count"] == 1
        assert second_json["deactivated_count"] == 1
        assert second_json["unchanged_count"] == 0
        assert second_json["active_usernames"] == ["bob"]

        listed_after_second = super_client.get(
            f"/api/admin/assignments?questionnaire_version_id={version_id}"
        )
        assert listed_after_second.status_code == 200
        by_username = {item["username"]: item for item in listed_after_second.json()}
        assert by_username["alice"]["is_active"] is False
        assert by_username["bob"]["is_active"] is True
