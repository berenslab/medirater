import csv
import io

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

        uploaded_asset = admin_a_client.post(
            "/api/admin/assets/upload",
            data={"questionnaire_id": questionnaire_id},
            files=[("files", ("Task1/1.png", b"fake-image-bytes", "image/png"))],
        )
        assert uploaded_asset.status_code == 200
        uploaded_asset_json = uploaded_asset.json()
        assert len(uploaded_asset_json) == 1
        asset_id = uploaded_asset_json[0]["id"]

        question = admin_a_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "How severe is this case?",
                "question_type": "single_choice",
                "is_required": True,
                "config": {
                    "case_key": "case-0001",
                    "stimulus_asset_ids": [asset_id],
                },
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

        user_token_for_draft = admin_a_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert user_token_for_draft.status_code == 200

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

    with TestClient(app) as draft_user_client:
        _signup(draft_user_client, "bob", token=user_token_for_draft.json()["token"])

        consent = draft_user_client.post(
            f"/api/user/questionnaires/{version_id}/consent",
            json={"consented": True},
        )
        assert consent.status_code == 200

        saved_draft = draft_user_client.post(
            f"/api/user/questionnaires/{version_id}/draft",
            json={"answers": [{"question_id": question_id, "value": "mild"}]},
        )
        assert saved_draft.status_code == 200

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
        assert len(response_items) == 2
        by_username = {item["username"]: item for item in response_items}
        assert by_username["alice"]["response_status"] == "submitted"
        assert by_username["bob"]["response_status"] == "in_progress"
        submitted_response_id = by_username["alice"]["response_id"]
        draft_response_id = by_username["bob"]["response_id"]
        assert by_username["alice"]["submitted_at"] is not None
        assert by_username["bob"]["submitted_at"] is None
        assert by_username["bob"]["saved_at"] is not None

        in_progress_only = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses?response_status=in_progress"
        )
        assert in_progress_only.status_code == 200
        in_progress_items = in_progress_only.json()
        assert len(in_progress_items) == 1
        assert in_progress_items[0]["username"] == "bob"
        assert in_progress_items[0]["response_status"] == "in_progress"

        response_detail = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/{submitted_response_id}"
        )
        assert response_detail.status_code == 200
        detail_json = response_detail.json()
        assert detail_json["username"] == "alice"
        assert detail_json["response_status"] == "submitted"
        assert len(detail_json["items"]) == 1
        assert detail_json["items"][0]["question_prompt_text"] == "How severe is this case?"
        assert detail_json["items"][0]["answer_value"] == "severe"

        draft_detail = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/{draft_response_id}"
        )
        assert draft_detail.status_code == 200
        draft_detail_json = draft_detail.json()
        assert draft_detail_json["username"] == "bob"
        assert draft_detail_json["response_status"] == "in_progress"
        assert len(draft_detail_json["items"]) == 1
        assert draft_detail_json["items"][0]["answer_value"] == "mild"

        exported_csv = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/export.csv"
        )
        assert exported_csv.status_code == 200
        assert exported_csv.headers["content-type"].startswith("text/csv")
        assert "attachment;" in exported_csv.headers.get("content-disposition", "")
        csv_body = exported_csv.text
        assert "questionnaire_title" in csv_body
        assert "username" in csv_body
        assert "v1_q1" not in csv_body
        csv_rows = list(csv.DictReader(io.StringIO(csv_body)))
        assert len(csv_rows) == 2
        csv_by_username = {row["username"]: row for row in csv_rows}
        alice_row = csv_by_username["alice"]
        bob_row = csv_by_username["bob"]
        assert alice_row["answer_value"] == "severe"
        assert alice_row["response_status"] == "submitted"
        assert bob_row["answer_value"] == "mild"
        assert bob_row["response_status"] == "in_progress"
        assert alice_row["question_identifier"] == "c1q1"
        assert alice_row["case_key"] == "case-0001"
        assert alice_row["case_image_filenames"] == "Task1/1.png"

        exported_csv_filtered = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/export.csv?version_id={version_id}"
        )
        assert exported_csv_filtered.status_code == 200
        assert exported_csv_filtered.text == csv_body

        exported_csv_in_progress = admin_a_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/export.csv?version_id={version_id}&response_status=in_progress"
        )
        assert exported_csv_in_progress.status_code == 200
        in_progress_rows = list(csv.DictReader(io.StringIO(exported_csv_in_progress.text)))
        assert len(in_progress_rows) == 1
        assert in_progress_rows[0]["username"] == "bob"
        assert in_progress_rows[0]["response_status"] == "in_progress"

    with TestClient(app) as admin_b_client:
        _signup(admin_b_client, "admin_b", token=admin_b_token.json()["token"])

        forbidden_api = admin_b_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses"
        )
        assert forbidden_api.status_code == 403

        forbidden_export = admin_b_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/export.csv"
        )
        assert forbidden_export.status_code == 403

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
        assert len(response_list.json()) == 2

        response_id = response_list.json()[0]["response_id"]
        response_detail = super_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/{response_id}"
        )
        assert response_detail.status_code == 200
        assert response_detail.json()["items"][0]["answer_value"] in {"severe", "mild"}

        super_export = super_client.get(
            f"/api/admin/questionnaires/{questionnaire_id}/responses/export.csv"
        )
        assert super_export.status_code == 200
        assert "alice" in super_export.text
