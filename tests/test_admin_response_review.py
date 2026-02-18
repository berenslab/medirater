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


def test_admin_and_superadmin_can_review_questionnaire_responses(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)

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

    with TestClient(app) as admin_a_client:
        _signup(admin_a_client, "admin_a", token=admin_a_token.json()["token"])

        created = admin_a_client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Response review demo",
                "description": "demo",
                "instructions_markdown": "answer honestly",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert questionnaire_id
        assert version_id

        question = admin_a_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "How severe is this case?",
                "question_type": "single_choice",
                "is_required": True,
                "config": {},
                "choices": [
                    {"position": 1, "label": "Mild", "value": "mild"},
                    {"position": 2, "label": "Severe", "value": "severe"},
                ],
            },
        )
        assert question.status_code == 200
        question_id = question.json()["id"]

        published = admin_a_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
        )
        assert published.status_code == 200

        user_token = admin_a_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert user_token.status_code == 200

    with TestClient(app) as user_client:
        _signup(user_client, "alice", token=user_token.json()["token"])

        consent = user_client.post(
            f"/api/user/questionnaires/{version_id}/consent",
            json={"consented": True},
        )
        assert consent.status_code == 200

        submitted = user_client.post(
            f"/api/user/questionnaires/{version_id}/responses",
            json={"answers": [{"question_id": question_id, "value": "severe"}]},
        )
        assert submitted.status_code == 200

    with TestClient(app) as admin_a_client:
        _login(admin_a_client, "admin_a")

        responses_page = admin_a_client.get(
            f"/questionnaires/{questionnaire_id}/responses",
            follow_redirects=False,
        )
        assert responses_page.status_code == 200

        response_list = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses"
        )
        assert response_list.status_code == 200
        response_items = response_list.json()
        assert len(response_items) == 1
        assert response_items[0]["username"] == "alice"
        response_id = response_items[0]["response_id"]

        response_detail = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/{response_id}"
        )
        assert response_detail.status_code == 200
        detail_json = response_detail.json()
        assert detail_json["username"] == "alice"
        assert len(detail_json["items"]) == 1
        assert detail_json["items"][0]["question_prompt_text"] == "How severe is this case?"
        assert detail_json["items"][0]["answer_value"] == "severe"

    with TestClient(app) as admin_b_client:
        _signup(admin_b_client, "admin_b", token=admin_b_token.json()["token"])

        forbidden_api = admin_b_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses"
        )
        assert forbidden_api.status_code == 403

        forbidden_page = admin_b_client.get(
            f"/questionnaires/{questionnaire_id}/responses",
            follow_redirects=False,
        )
        assert forbidden_page.status_code == 303
        assert forbidden_page.headers["location"] == "/questionnaires"

    with TestClient(app) as super_client:
        _login(super_client, "root")

        response_list = super_client.get(f"/api/admin/questionnaires/{questionnaire_id}/responses")
        assert response_list.status_code == 200
        assert len(response_list.json()) == 1

        response_id = response_list.json()[0]["response_id"]
        response_detail = super_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/{response_id}"
        )
        assert response_detail.status_code == 200
        assert response_detail.json()["items"][0]["answer_value"] == "severe"
