from sqlalchemy import func, select

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import (
    Asset,
    Choice,
    Question,
    Questionnaire,
    QuestionnaireConsent,
    QuestionnaireVersion,
    Response,
    ResponseItem,
    Role,
    UserAssignment,
)
from app.services.token_service import issue_signup_token

# 1x1 PNG
PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C636018050000080001B20A2DB40000000049454E44AE426082"
)


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


def test_admin_questionnaire_lifecycle_and_draft_clone(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)

        admin_token_resp = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        )
        assert admin_token_resp.status_code == 200
        admin_token = admin_token_resp.json()["token"]

    with TestClient(app) as admin_client:
        _signup(admin_client, "designer", token=admin_token)

        created = admin_client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Retina Screening V1",
                "description": "First draft",
                "instructions_markdown": "Please answer all questions.",
            },
        )
        assert created.status_code == 200
        questionnaire = created.json()
        questionnaire_id = questionnaire["id"]
        draft_version_id = questionnaire["latest_version_id"]
        assert questionnaire["owner_admin_username"] == "designer"
        assert questionnaire["slug"] == "retina-screening-v1"
        assert draft_version_id

        created_duplicate_title = admin_client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Retina Screening V1",
                "description": "Second",
                "instructions_markdown": "",
            },
        )
        assert created_duplicate_title.status_code == 200
        assert created_duplicate_title.json()["slug"] == "retina-screening-v1-2"

        custom_slug_questionnaire = admin_client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Custom Slug Questionnaire",
                "slug": "custom-slug",
                "description": None,
                "instructions_markdown": "",
            },
        )
        assert custom_slug_questionnaire.status_code == 200
        custom_id = custom_slug_questionnaire.json()["id"]
        assert custom_slug_questionnaire.json()["slug"] == "custom-slug"

        updated_custom_slug = admin_client.patch(
            f"/api/admin/questionnaires/{custom_id}",
            json={
                "title": "Custom Slug Questionnaire",
                "slug": "retina-screening-v1",
                "description": None,
            },
        )
        assert updated_custom_slug.status_code == 200
        assert updated_custom_slug.json()["slug"].startswith("retina-screening-v1")

        created_question = admin_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{draft_version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "What is your interpretation?",
                "question_type": "single_choice",
                "is_required": True,
                "config": {"show_image": True},
                "choices": [
                    {"position": 1, "label": "Normal", "value": "normal"},
                    {"position": 2, "label": "Abnormal", "value": "abnormal"},
                ],
            },
        )
        assert created_question.status_code == 200

        published = admin_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{draft_version_id}/publish"
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"

        mutate_published = admin_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{draft_version_id}/questions",
            json={
                "position": 2,
                "prompt_text": "Should not work on published",
                "question_type": "short_text",
                "is_required": False,
                "config": {},
                "choices": [],
            },
        )
        assert mutate_published.status_code == 400

        created_v2 = admin_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions",
            json={"instructions_markdown": "Second draft"},
        )
        assert created_v2.status_code == 200
        draft_v2_id = created_v2.json()["id"]
        assert created_v2.json()["status"] == "draft"
        assert created_v2.json()["version_number"] == 2

        v2_detail = admin_client.get(f"/api/admin/questionnaires/{questionnaire_id}/versions/{draft_v2_id}")
        assert v2_detail.status_code == 200
        v2_questions = v2_detail.json()["questions"]
        assert len(v2_questions) == 1
        assert v2_questions[0]["choices"][0]["value"] == "normal"


def test_admin_ownership_enforced_and_superadmin_can_override(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)

        admin_a_token = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]
        admin_b_token = super_client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

        with TestClient(app) as admin_a_client:
            _signup(admin_a_client, "author_a", token=admin_a_token)
            created = admin_a_client.post(
                "/api/admin/questionnaires",
                json={"title": "A's Questionnaire", "description": None, "instructions_markdown": ""},
            )
            assert created.status_code == 200
            questionnaire_id = created.json()["id"]
            version_id = created.json()["latest_version_id"]
            assert version_id

            published = admin_a_client.post(
                f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
            )
            assert published.status_code == 200

        with TestClient(app) as admin_b_client:
            _signup(admin_b_client, "author_b", token=admin_b_token)

            forbidden_get = admin_b_client.get(f"/api/admin/questionnaires/{questionnaire_id}")
            assert forbidden_get.status_code == 403

            forbidden_patch = admin_b_client.patch(
                f"/api/admin/questionnaires/{questionnaire_id}",
                json={"title": "Hacked", "description": "nope"},
            )
            assert forbidden_patch.status_code == 403

            forbidden_scope = admin_b_client.post(
                "/api/admin/signup-tokens",
                json={
                    "role": "user",
                    "expires_in_minutes": 60,
                    "questionnaire_version_ids": [version_id],
                },
            )
            assert forbidden_scope.status_code == 403

        allowed_patch = super_client.patch(
            f"/api/admin/questionnaires/{questionnaire_id}",
            json={"title": "Superadmin Updated", "description": "allowed"},
        )
        assert allowed_patch.status_code == 200

        allowed_scope = super_client.post(
            "/api/admin/signup-tokens",
            json={
                "role": "user",
                "expires_in_minutes": 60,
                "questionnaire_version_ids": [version_id],
            },
        )
        assert allowed_scope.status_code == 200


def test_delete_questionnaire_cascades_assets_and_answers(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as super_client:
        _signup(super_client, "root", token=bootstrap_token)

        created = super_client.post(
            "/api/admin/questionnaires",
            json={"title": "Delete me", "description": None, "instructions_markdown": "instructions"},
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        uploaded = super_client.post(
            "/api/admin/assets/upload",
            data={"questionnaire_id": questionnaire_id},
            files=[("files", ("Task1/1a.png", PNG_BYTES, "image/png"))],
        )
        assert uploaded.status_code == 200

        question = super_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions",
            json={
                "position": 1,
                "prompt_text": "Write your answer",
                "question_type": "short_text",
                "is_required": True,
                "config": {"stimulus_asset_ids": [uploaded.json()[0]["id"]]},
                "choices": [],
            },
        )
        assert question.status_code == 200
        question_id = question.json()["id"]

        published = super_client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish"
        )
        assert published.status_code == 200

        user_token = super_client.post(
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
            json={"answers": [{"question_id": question_id, "value": "ok"}]},
        )
        assert submitted.status_code == 200

    with TestClient(app) as super_client:
        begin = super_client.post("/api/auth/login/begin", json={"username": "root"})
        assert begin.status_code == 200
        complete = super_client.post(
            "/api/auth/login/complete",
            json={
                "challenge_id": begin.json()["challenge_id"],
                "credential": {"id": "cred-root-1"},
            },
        )
        assert complete.status_code == 200

        deleted = super_client.delete(f"/api/admin/questionnaires/{questionnaire_id}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True

    with test_session_factory() as db:
        assert db.get(Questionnaire, questionnaire_id) is None
        assert db.scalar(
            select(func.count(QuestionnaireVersion.id)).where(
                QuestionnaireVersion.questionnaire_id == questionnaire_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(Question.id)).where(Question.questionnaire_version_id == version_id)
        ) == 0
        assert db.scalar(
            select(func.count(Choice.id)).join(Question, Question.id == Choice.question_id).where(
                Question.questionnaire_version_id == version_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(Response.id)).where(Response.questionnaire_version_id == version_id)
        ) == 0
        assert db.scalar(
            select(func.count(ResponseItem.id))
            .join(Response, Response.id == ResponseItem.response_id)
            .where(Response.questionnaire_version_id == version_id)
        ) == 0
        assert db.scalar(
            select(func.count(UserAssignment.id)).where(
                UserAssignment.questionnaire_version_id == version_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(QuestionnaireConsent.id)).where(
                QuestionnaireConsent.questionnaire_version_id == version_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(Asset.id)).where(Asset.questionnaire_id == questionnaire_id)
        ) == 0
