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


def test_public_pages_and_route_guards() -> None:
    with TestClient(app) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 307
        assert home.headers["location"] == "/login"

        signup = client.get("/signup")
        assert signup.status_code == 200
        assert "Signup" in signup.text

        admin_signup = client.get("/admin_signup")
        assert admin_signup.status_code == 200
        assert "Admin or Superadmin signup" in admin_signup.text

        login = client.get("/login")
        assert login.status_code == 200
        assert "Login with passkey" in login.text

        questionnaires = client.get("/questionnaires", follow_redirects=False)
        assert questionnaires.status_code == 303
        assert questionnaires.headers["location"] == "/login"

        users = client.get("/users", follow_redirects=False)
        assert users.status_code == 303
        assert users.headers["location"] == "/login"

        settings = client.get("/settings", follow_redirects=False)
        assert settings.status_code == 303
        assert settings.headers["location"] == "/login"

        assigned = client.get("/assigned", follow_redirects=False)
        assert assigned.status_code == 303
        assert assigned.headers["location"] == "/login"

        answer = client.get("/answer/fake-version", follow_redirects=False)
        assert answer.status_code == 303
        assert answer.headers["location"] == "/login"

        consent = client.get("/answer/fake-version/consent", follow_redirects=False)
        assert consent.status_code == 303
        assert consent.headers["location"] == "/login"

        responses = client.get("/questionnaires/fake-questionnaire/responses", follow_redirects=False)
        assert responses.status_code == 303
        assert responses.headers["location"] == "/login"

        assignments = client.get("/questionnaires/fake-questionnaire/assignments", follow_redirects=False)
        assert assignments.status_code == 303
        assert assignments.headers["location"] == "/login"

        admin = client.get("/admin", follow_redirects=False)
        assert admin.status_code == 303
        assert admin.headers["location"] == "/questionnaires"

        passkeys = client.get("/passkeys", follow_redirects=False)
        assert passkeys.status_code == 303
        assert passkeys.headers["location"] == "/settings"

        me = client.get("/me", follow_redirects=False)
        assert me.status_code == 303
        assert me.headers["location"] == "/"


def test_logged_in_regular_user_lands_on_assigned() -> None:
    with TestClient(app) as client:
        _signup(client, "alice")

        login = client.get("/login", follow_redirects=False)
        assert login.status_code == 303
        assert login.headers["location"] == "/assigned"

        signup = client.get("/signup", follow_redirects=False)
        assert signup.status_code == 303
        assert signup.headers["location"] == "/assigned"

        admin_signup = client.get("/admin_signup", follow_redirects=False)
        assert admin_signup.status_code == 303
        assert admin_signup.headers["location"] == "/assigned"

        questionnaires = client.get("/questionnaires", follow_redirects=False)
        assert questionnaires.status_code == 303
        assert questionnaires.headers["location"] == "/assigned"

        users = client.get("/users", follow_redirects=False)
        assert users.status_code == 303
        assert users.headers["location"] == "/assigned"

        assigned = client.get("/assigned")
        assert assigned.status_code == 200
        assert "Assigned Questionnaires" in assigned.text
        assert "href=\"/assigned\"" in assigned.text
        assert "href=\"/settings\"" in assigned.text

        settings = client.get("/settings")
        assert settings.status_code == 200
        assert "Settings" in settings.text
        assert "href=\"/questionnaires\"" not in settings.text
        assert "href=\"/users\"" not in settings.text
        assert "href=\"/assigned\"" in settings.text

        answer = client.get("/answer/fake-version", follow_redirects=False)
        assert answer.status_code == 303
        assert answer.headers["location"] == "/assigned"

        consent = client.get("/answer/fake-version/consent", follow_redirects=False)
        assert consent.status_code == 303
        assert consent.headers["location"] == "/assigned"

        responses = client.get("/questionnaires/fake-questionnaire/responses", follow_redirects=False)
        assert responses.status_code == 303
        assert responses.headers["location"] == "/assigned"

        assignments = client.get("/questionnaires/fake-questionnaire/assignments", follow_redirects=False)
        assert assignments.status_code == 303
        assert assignments.headers["location"] == "/assigned"


def test_superadmin_lands_on_questionnaires_with_expected_nav(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        home = client.get("/", follow_redirects=False)
        assert home.status_code == 303
        assert home.headers["location"] == "/questionnaires"

        questionnaires = client.get("/questionnaires")
        assert questionnaires.status_code == 200
        assert "Questionnaires" in questionnaires.text
        assert "href=\"/users\"" in questionnaires.text
        assert "href=\"/settings\"" in questionnaires.text
        assert "href=\"/assigned\"" not in questionnaires.text
        assert "/responses" in questionnaires.text
        assert "/assignments" in questionnaires.text

        users = client.get("/users")
        assert users.status_code == 200
        assert "User Management" in users.text

        settings = client.get("/settings")
        assert settings.status_code == 200
        assert "Signup mode" in settings.text
        assert "Signup tokens and questionnaire visibility" in settings.text


def test_answer_page_uses_recipe_specific_template_when_available(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Patch layout dispatch",
                "description": None,
                "instructions_markdown": "",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        created_question = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "Patch prompt",
                "question_type": "short_text",
                "is_required": False,
                "config": {
                    "case_key": "case-0001",
                    "recipe_type": "case_with_patches",
                    "stimulus_asset_ids": [],
                    "patch_asset_id": "patch-asset-1",
                    "patch_index": 1,
                },
                "choices": [],
            },
        )
        assert created_question.status_code == 200

        published = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
        )
        assert published.status_code == 200

        consented = client.post(
            f"/api/user/questionnaires/{version_id}/consent",
            json={"consented": True},
        )
        assert consented.status_code == 200

        answer_page = client.get(f"/answer/{version_id}")
        assert answer_page.status_code == 200
        assert 'const ANSWER_LAYOUT_MODE = "case_and_question_images";' in answer_page.text


def test_answer_page_requires_template_for_unknown_recipe(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Unknown recipe dispatch",
                "description": None,
                "instructions_markdown": "",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        created_question = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "Custom recipe prompt",
                "question_type": "short_text",
                "is_required": False,
                "config": {
                    "case_key": "case-0001",
                    "recipe_type": "custom_recipe_demo",
                    "stimulus_asset_ids": [],
                },
                "choices": [],
            },
        )
        assert created_question.status_code == 200

        published = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
        )
        assert published.status_code == 200

        consented = client.post(
            f"/api/user/questionnaires/{version_id}/consent",
            json={"consented": True},
        )
        assert consented.status_code == 200

        answer_page = client.get(f"/answer/{version_id}")
        assert answer_page.status_code == 500
        assert answer_page.json()["detail"] == "Missing answer template for recipe 'custom_recipe_demo'"
