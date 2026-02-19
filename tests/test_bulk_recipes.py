from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Role
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


def _upload_assets(client: TestClient, questionnaire_id: str, relative_paths: list[str]) -> list[str]:
    files = [
        ("files", (path, PNG_BYTES, "image/png"))
        for path in relative_paths
    ]
    response = client.post(
        "/api/admin/assets/upload",
        data={"questionnaire_id": questionnaire_id},
        files=files,
    )
    assert response.status_code == 200
    return [item["id"] for item in response.json()]


def test_bulk_recipe_catalog_lists_supported_recipes(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        response = client.get("/api/admin/questionnaires/bulk-recipes/catalog")
        assert response.status_code == 200
        items = response.json()
        assert isinstance(items, list)
        assert len(items) >= 4

        by_type = {item["recipe_type"]: item for item in items}
        assert "indexed_suffix_sets" in by_type
        assert "single_per_file" in by_type
        assert "paired_by_filename" in by_type
        assert "case_with_patches" in by_type
        assert "triplet_by_suffix" not in by_type

        assert by_type["indexed_suffix_sets"]["title"] == "Numbered Image Sets"
        assert by_type["indexed_suffix_sets"]["instructions"]
        assert by_type["case_with_patches"]["supports_patch_question_template"] is True
        assert by_type["case_with_patches"]["example_paths"]


def test_assets_are_scoped_to_questionnaire(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        q1 = client.post(
            "/api/admin/questionnaires",
            json={"title": "Scoped A", "description": None, "instructions_markdown": ""},
        )
        assert q1.status_code == 200
        q1_id = q1.json()["id"]

        q2 = client.post(
            "/api/admin/questionnaires",
            json={"title": "Scoped B", "description": None, "instructions_markdown": ""},
        )
        assert q2.status_code == 200
        q2_id = q2.json()["id"]

        q1_asset_ids = _upload_assets(client, q1_id, ["A/001.png"])
        q2_asset_ids = _upload_assets(client, q2_id, ["B/001.png"])

        list_q1 = client.get(f"/api/admin/assets?questionnaire_id={q1_id}")
        assert list_q1.status_code == 200
        assert [item["id"] for item in list_q1.json()] == q1_asset_ids

        list_q2 = client.get(f"/api/admin/assets?questionnaire_id={q2_id}")
        assert list_q2.status_code == 200
        assert [item["id"] for item in list_q2.json()] == q2_asset_ids


def test_questionnaire_assets_require_delete_before_reupload(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)

        created = client.post(
            "/api/admin/questionnaires",
            json={"title": "Single upload", "description": None, "instructions_markdown": ""},
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]

        first_upload = client.post(
            "/api/admin/assets/upload",
            data={"questionnaire_id": questionnaire_id},
            files=[("files", ("A/001.png", PNG_BYTES, "image/png"))],
        )
        assert first_upload.status_code == 200

        second_upload = client.post(
            "/api/admin/assets/upload",
            data={"questionnaire_id": questionnaire_id},
            files=[("files", ("A/002.png", PNG_BYTES, "image/png"))],
        )
        assert second_upload.status_code == 400

        deleted = client.delete(f"/api/admin/assets/questionnaire/{questionnaire_id}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True

        upload_after_delete = client.post(
            "/api/admin/assets/upload",
            data={"questionnaire_id": questionnaire_id},
            files=[("files", ("A/003.png", PNG_BYTES, "image/png"))],
        )
        assert upload_after_delete.status_code == 200


def test_indexed_suffix_sets_recipe_preview_and_apply(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        admin_token = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

    with TestClient(app) as client:
        _signup(client, "designer", token=admin_token)
        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Triplet bulk",
                "description": "bulk test",
                "instructions_markdown": "instr",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        asset_ids = _upload_assets(
            client,
            questionnaire_id,
            [
                "Fundus/Task1/1a.png",
                "Fundus/Task1/1b.png",
                "Fundus/Task1/1c.png",
                "Fundus/Task1/2a.png",
                "Fundus/Task1/2b.png",
                "Fundus/Task1/2c.png",
            ],
        )

        preview = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview",
            json={
                "recipe_type": "indexed_suffix_sets",
                "asset_ids": asset_ids,
                "recipe_config": {"images_per_case": 3},
                "question_templates": [
                    {
                        "prompt_template": "Case {case_index}/{case_total}: Which image is AI generated?",
                        "question_type": "single_choice",
                        "is_required": True,
                        "choices": [
                            {"label": "a", "value": "a"},
                            {"label": "b", "value": "b"},
                            {"label": "c", "value": "c"},
                        ],
                    }
                ],
            },
        )
        assert preview.status_code == 200
        preview_json = preview.json()
        assert len(preview_json["cases"]) == 2
        assert all(len(case["stimulus_asset_ids"]) == 3 for case in preview_json["cases"])
        assert all(len(case["questions"]) == 1 for case in preview_json["cases"])

        applied = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply",
            json={
                "recipe_type": "indexed_suffix_sets",
                "asset_ids": asset_ids,
                "recipe_config": {"images_per_case": 3},
                "replace_existing_questions": True,
                "question_templates": [
                    {
                        "prompt_template": "Case {case_index}/{case_total}: Which image is AI generated?",
                        "question_type": "single_choice",
                        "is_required": True,
                        "choices": [
                            {"label": "a", "value": "a"},
                            {"label": "b", "value": "b"},
                            {"label": "c", "value": "c"},
                        ],
                    }
                ],
            },
        )
        assert applied.status_code == 200
        assert applied.json()["created_questions"] == 2

        detail = client.get(f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}")
        assert detail.status_code == 200
        questions = detail.json()["questions"]
        assert len(questions) == 2
        for question in questions:
            assert len(question["config"]["stimulus_asset_ids"]) == 3
            assert question["question_type"] == "single_choice"


def test_indexed_suffix_sets_requires_consecutive_numbering(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        admin_token = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

    with TestClient(app) as client:
        _signup(client, "designer_gap", token=admin_token)
        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Number gap bulk",
                "description": "bulk test",
                "instructions_markdown": "instr",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        asset_ids = _upload_assets(
            client,
            questionnaire_id,
            [
                "Task/1.png",
                "Task/3.png",
            ],
        )

        preview = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview",
            json={
                "recipe_type": "indexed_suffix_sets",
                "asset_ids": asset_ids,
                "recipe_config": {"images_per_case": 1},
                "question_templates": [
                    {
                        "prompt_template": "Case {case_index}/{case_total}: any finding?",
                        "question_type": "single_choice",
                        "is_required": True,
                        "choices": [
                            {"label": "Yes", "value": "yes"},
                            {"label": "No", "value": "no"},
                        ],
                    }
                ],
            },
        )
        assert preview.status_code == 200
        payload = preview.json()
        assert len(payload["cases"]) == 2
        assert any("Missing case indices: 2" in warning for warning in payload["warnings"])


def test_indexed_suffix_sets_custom_stimulus_labels_are_saved(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        admin_token = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

    with TestClient(app) as client:
        _signup(client, "designer_labels", token=admin_token)
        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Custom stimulus labels",
                "description": "bulk test",
                "instructions_markdown": "instr",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        asset_ids = _upload_assets(
            client,
            questionnaire_id,
            [
                "Task/1a.png",
                "Task/1b.png",
                "Task/2a.png",
                "Task/2b.png",
            ],
        )

        payload = {
            "recipe_type": "indexed_suffix_sets",
            "asset_ids": asset_ids,
            "recipe_config": {
                "images_per_case": 2,
                "stimulus_slot_labels": ["Original", "Annotated"],
            },
            "replace_existing_questions": True,
            "question_templates": [
                {
                    "prompt_template": "Case {case_index}/{case_total}",
                    "question_type": "single_choice",
                    "is_required": True,
                    "choices": [
                        {"label": "Original", "value": "original"},
                        {"label": "Annotated", "value": "annotated"},
                    ],
                }
            ],
        }

        applied = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply",
            json=payload,
        )
        assert applied.status_code == 200
        assert applied.json()["created_questions"] == 2

        detail = client.get(f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}")
        assert detail.status_code == 200
        questions = detail.json()["questions"]
        assert len(questions) == 2
        assert all(
            question["config"].get("stimulus_labels") == ["Original", "Annotated"]
            for question in questions
        )


def test_case_with_patches_recipe_preview_and_apply(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        admin_token = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

    with TestClient(app) as client:
        _signup(client, "designer2", token=admin_token)
        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Case patch bulk",
                "description": "bulk test",
                "instructions_markdown": "instr",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        asset_ids = _upload_assets(
            client,
            questionnaire_id,
            [
                "ERM_Macula/case-001/img/main.png",
                "ERM_Macula/case-001/patch/p_1.png",
                "ERM_Macula/case-001/patch/p_2.png",
                "ERM_Macula/case-002/img/main.png",
                "ERM_Macula/case-002/patch/p_1.png",
            ],
        )

        body = {
            "recipe_type": "case_with_patches",
            "asset_ids": asset_ids,
            "recipe_config": {"img_folder": "img", "patch_folder": "patch", "strict": True},
            "question_templates": [
                {
                    "prompt_template": "Review case {case_key}",
                    "question_type": "short_text",
                    "is_required": True,
                    "choices": [],
                }
            ],
            "patch_question_template": {
                "prompt_template": "Patch {patch_index}/{patch_total} for {case_key}: ERM present?",
                "question_type": "single_choice",
                "is_required": True,
                "choices": [
                    {"label": "Yes", "value": "yes"},
                    {"label": "No", "value": "no"},
                    {"label": "Unsure", "value": "unsure"},
                ],
            },
        }

        preview = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview",
            json=body,
        )
        assert preview.status_code == 200
        preview_json = preview.json()
        assert len(preview_json["cases"]) == 2
        # Case 1: 1 base + 2 patch questions, Case 2: 1 base + 1 patch question.
        question_counts = sorted(len(case["questions"]) for case in preview_json["cases"])
        assert question_counts == [2, 3]

        applied = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply",
            json={**body, "replace_existing_questions": True},
        )
        assert applied.status_code == 200
        assert applied.json()["created_questions"] == 5

        detail = client.get(f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}")
        assert detail.status_code == 200
        questions = detail.json()["questions"]
        assert len(questions) == 5
        patch_questions = [q for q in questions if "patch_asset_id" in q["config"]]
        assert len(patch_questions) == 3


def test_apply_edited_preview_questions(test_session_factory) -> None:
    bootstrap_token = _bootstrap_superadmin_token(test_session_factory)

    with TestClient(app) as client:
        _signup(client, "root", token=bootstrap_token)
        admin_token = client.post(
            "/api/admin/signup-tokens",
            json={"role": "admin", "expires_in_minutes": 60},
        ).json()["token"]

    with TestClient(app) as client:
        _signup(client, "designer3", token=admin_token)
        created = client.post(
            "/api/admin/questionnaires",
            json={
                "title": "Preview edit apply",
                "description": "bulk test",
                "instructions_markdown": "instr",
            },
        )
        assert created.status_code == 200
        questionnaire_id = created.json()["id"]
        version_id = created.json()["latest_version_id"]
        assert version_id

        asset_ids = _upload_assets(client, questionnaire_id, ["OCT/001a.png"])
        preview = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview",
            json={
                "recipe_type": "single_per_file",
                "asset_ids": asset_ids,
                "recipe_config": {},
                "question_templates": [
                    {
                        "prompt_template": "Case {case_index}/{case_total}: initial prompt",
                        "question_type": "short_text",
                        "is_required": True,
                        "choices": [],
                    }
                ],
            },
        )
        assert preview.status_code == 200
        preview_json = preview.json()
        assert len(preview_json["cases"]) == 1

        edited_cases = preview_json["cases"]
        edited_cases[0]["questions"][0]["prompt_text"] = "Edited prompt from frontend"
        edited_cases[0]["questions"][0]["question_type"] = "single_choice"
        edited_cases[0]["questions"][0]["choices"] = [
            {"label": "Normal", "value": "normal"},
            {"label": "Abnormal", "value": "abnormal"},
        ]

        applied = client.post(
            f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply-preview",
            json={
                "replace_existing_questions": True,
                "cases": edited_cases,
            },
        )
        assert applied.status_code == 200
        assert applied.json()["created_questions"] == 1

        detail = client.get(f"/api/admin/questionnaires/{questionnaire_id}/versions/{version_id}")
        assert detail.status_code == 200
        questions = detail.json()["questions"]
        assert len(questions) == 1
        assert questions[0]["prompt_text"] == "Edited prompt from frontend"
        assert questions[0]["question_type"] == "single_choice"
        assert [item["value"] for item in questions[0]["choices"]] == ["normal", "abnormal"]
