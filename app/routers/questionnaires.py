import json
import csv
import io
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_admin_user
from app.models import (
    Asset,
    Choice,
    Question,
    QuestionType,
    QuestionnaireConsent,
    Questionnaire,
    QuestionnaireVersion,
    QuestionnaireVersionStatus,
    Response,
    ResponseItem,
    Role,
    User,
    UserAssignment,
    utcnow,
)
from app.schemas import (
    AdminResponseDetailOut,
    AdminResponseItemOut,
    AdminResponseSummaryOut,
    BulkGeneratedQuestionPreview,
    BulkRecipeApplyRequest,
    BulkRecipeApplyPreviewRequest,
    BulkRecipeApplyResponse,
    BulkRecipeCasePreview,
    BulkRecipePreviewRequest,
    BulkRecipePreviewResponse,
    BulkRecipeQuestionTemplate,
    ChoiceOut,
    QuestionCreate,
    QuestionOut,
    QuestionUpdate,
    QuestionnaireCreateRequest,
    QuestionnaireDetailOut,
    QuestionnaireSummaryOut,
    QuestionnaireUpdateRequest,
    QuestionnaireVersionCreateRequest,
    QuestionnaireVersionDetailOut,
    QuestionnaireVersionSummaryOut,
    QuestionnaireVersionUpdateRequest,
)
from app.security import normalize_username
from app.services.bulk_recipe_service import GroupedCase, group_assets_for_recipe
from app.services.consent_service import normalize_optional_consent_text

router = APIRouter(prefix="/api/admin/questionnaires", tags=["questionnaires"])


def _is_superadmin(user: User) -> bool:
    return user.role == Role.SUPERADMIN


def _to_choice_out(choice: Choice) -> ChoiceOut:
    return ChoiceOut.model_validate(choice)


def _parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _parse_json_any(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _stringify_answer_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _normalize_slug(raw_slug: str) -> str:
    base = raw_slug.strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug must contain at least one letter or number",
        )
    if len(base) > 220:
        base = base[:220].rstrip("-")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug must contain at least one letter or number",
        )
    return base


def _build_unique_slug(
    db: Session,
    *,
    requested_slug: str,
    exclude_questionnaire_id: str | None = None,
) -> str:
    base_slug = _normalize_slug(requested_slug)
    candidate = base_slug
    suffix = 2
    while True:
        stmt = select(Questionnaire.id).where(Questionnaire.slug == candidate)
        if exclude_questionnaire_id:
            stmt = stmt.where(Questionnaire.id != exclude_questionnaire_id)
        existing = db.scalar(stmt)
        if not existing:
            return candidate

        suffix_text = f"-{suffix}"
        allowed_base_len = max(1, 220 - len(suffix_text))
        candidate = f"{base_slug[:allowed_base_len].rstrip('-')}{suffix_text}"
        suffix += 1


def _to_question_out(question: Question) -> QuestionOut:
    choices = sorted(question.choices, key=lambda item: item.position)
    return QuestionOut(
        id=question.id,
        position=question.position,
        prompt_text=question.prompt_text,
        question_type=question.question_type,
        is_required=question.is_required,
        config=_parse_json_object(question.config_json),
        choices=[_to_choice_out(choice) for choice in choices],
    )


def _to_version_summary_out(version: QuestionnaireVersion) -> QuestionnaireVersionSummaryOut:
    return QuestionnaireVersionSummaryOut(
        id=version.id,
        questionnaire_id=version.questionnaire_id,
        version_number=version.version_number,
        status=version.status,
        instructions_markdown=version.instructions_markdown,
        consent_text=version.consent_text,
        published_at=version.published_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _to_version_detail_out(version: QuestionnaireVersion) -> QuestionnaireVersionDetailOut:
    questions = sorted(version.questions, key=lambda item: item.position)
    return QuestionnaireVersionDetailOut(
        **_to_version_summary_out(version).model_dump(),
        questions=[_to_question_out(question) for question in questions],
    )


def _to_questionnaire_summary_out(questionnaire: Questionnaire) -> QuestionnaireSummaryOut:
    latest_version = max(
        questionnaire.versions,
        key=lambda item: item.version_number,
        default=None,
    )
    return QuestionnaireSummaryOut(
        id=questionnaire.id,
        owner_admin_username=questionnaire.owner_admin.username,
        slug=questionnaire.slug,
        title=questionnaire.title,
        description=questionnaire.description,
        is_archived=questionnaire.is_archived,
        created_at=questionnaire.created_at,
        updated_at=questionnaire.updated_at,
        latest_version_id=latest_version.id if latest_version else None,
        latest_version_status=latest_version.status if latest_version else None,
        latest_version_number=latest_version.version_number if latest_version else None,
    )


def _get_accessible_questionnaire(db: Session, *, questionnaire_id: str, user: User) -> Questionnaire:
    questionnaire = db.get(Questionnaire, questionnaire_id)
    if not questionnaire:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire not found")

    if not _is_superadmin(user) and questionnaire.owner_admin_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage your own questionnaires",
        )
    return questionnaire


def _get_version_for_questionnaire(
    db: Session,
    *,
    questionnaire_id: str,
    version_id: str,
) -> QuestionnaireVersion:
    version = db.scalar(
        select(QuestionnaireVersion)
        .where(QuestionnaireVersion.id == version_id)
        .where(QuestionnaireVersion.questionnaire_id == questionnaire_id)
    )
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire version not found")
    return version


def _ensure_draft(version: QuestionnaireVersion) -> None:
    if version.status != QuestionnaireVersionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft versions are editable",
        )


def _validate_question_payload(payload: QuestionCreate | QuestionUpdate) -> None:
    if payload.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE}:
        if not payload.choices:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choice-based questions must include at least one choice",
            )
    elif payload.choices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text questions cannot include choices",
        )

    positions = [choice.position for choice in payload.choices]
    if len(set(positions)) != len(positions):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choice positions must be unique within a question",
        )


def _validate_question_template(payload: BulkRecipeQuestionTemplate) -> None:
    if payload.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE}:
        if not payload.choices:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choice-based template questions must include at least one choice",
            )
    elif payload.choices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text template questions cannot include choices",
        )


def _validate_generated_question(payload: BulkGeneratedQuestionPreview) -> None:
    if not payload.prompt_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Generated questions must include prompt_text",
        )

    if payload.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE}:
        if not payload.choices:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choice-based generated questions must include at least one choice",
            )
    elif payload.choices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text generated questions cannot include choices",
        )

    values = [choice.value for choice in payload.choices]
    if len(values) != len(set(values)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choice values must be unique within a generated question",
        )


def _render_prompt(template: str, context: dict[str, Any]) -> str:
    try:
        return template.format(**context).strip()
    except KeyError as exc:
        missing_key = str(exc).strip("'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown template variable '{missing_key}' in prompt template",
        ) from exc


def _get_accessible_assets(
    db: Session,
    *,
    asset_ids: list[str],
    user: User,
    questionnaire_id: str,
) -> list[Asset]:
    normalized_asset_ids = [item.strip() for item in asset_ids if item.strip()]
    if not normalized_asset_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="asset_ids cannot be empty")

    stmt = select(Asset).where(Asset.id.in_(normalized_asset_ids))
    stmt = stmt.where(Asset.questionnaire_id == questionnaire_id)
    if not _is_superadmin(user):
        stmt = stmt.where(Asset.owner_user_id == user.id)
    assets = db.execute(stmt).scalars().all()
    found_ids = {asset.id for asset in assets}

    missing_ids = [asset_id for asset_id in normalized_asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or inaccessible asset ids: {', '.join(missing_ids)}",
        )

    return assets


def _build_bulk_preview(
    *,
    grouped_cases: list[GroupedCase],
    question_templates: list[BulkRecipeQuestionTemplate],
    patch_question_template: BulkRecipeQuestionTemplate | None,
    recipe_type: str,
    recipe_config: dict,
) -> list[BulkRecipeCasePreview]:
    preview_cases: list[BulkRecipeCasePreview] = []
    total_cases = len(grouped_cases)
    for case_index, grouped_case in enumerate(grouped_cases, start=1):
        stimulus_labels = [
            str(item).strip()
            for item in (grouped_case.stimulus_labels or recipe_config.get("stimulus_slot_labels", []))
            if str(item).strip()
        ]
        context = {
            "case_index": case_index,
            "case_total": total_cases,
            "case_key": grouped_case.case_key,
            "stimulus_count": len(grouped_case.stimulus_asset_ids),
            "patch_total": len(grouped_case.patch_asset_ids),
        }
        if stimulus_labels:
            context["stimulus_labels"] = ", ".join(stimulus_labels)
            for label_index, label in enumerate(stimulus_labels, start=1):
                context[f"stimulus_label_{label_index}"] = label

        generated_questions: list[BulkGeneratedQuestionPreview] = []
        for template in question_templates:
            _validate_question_template(template)
            prompt_text = _render_prompt(template.prompt_template, context)
            config = {
                "case_key": grouped_case.case_key,
                "recipe_type": recipe_type,
                "stimulus_asset_ids": grouped_case.stimulus_asset_ids,
                **({"stimulus_labels": stimulus_labels} if stimulus_labels else {}),
                **template.config,
            }
            generated_questions.append(
                BulkGeneratedQuestionPreview(
                    prompt_text=prompt_text,
                    question_type=template.question_type,
                    is_required=template.is_required,
                    choices=list(template.choices),
                    config=config,
                )
            )

        if patch_question_template:
            _validate_question_template(patch_question_template)
            for patch_index, patch_asset_id in enumerate(grouped_case.patch_asset_ids, start=1):
                patch_context = {
                    **context,
                    "patch_index": patch_index,
                    "patch_asset_id": patch_asset_id,
                }
                prompt_text = _render_prompt(
                    patch_question_template.prompt_template,
                    patch_context,
                )
                config = {
                    "case_key": grouped_case.case_key,
                    "recipe_type": recipe_type,
                    "stimulus_asset_ids": grouped_case.stimulus_asset_ids,
                    **({"stimulus_labels": stimulus_labels} if stimulus_labels else {}),
                    "patch_asset_id": patch_asset_id,
                    "patch_index": patch_index,
                    **patch_question_template.config,
                }
                generated_questions.append(
                    BulkGeneratedQuestionPreview(
                        prompt_text=prompt_text,
                        question_type=patch_question_template.question_type,
                        is_required=patch_question_template.is_required,
                        choices=list(patch_question_template.choices),
                        config=config,
                    )
                )

        preview_cases.append(
            BulkRecipeCasePreview(
                case_key=grouped_case.case_key,
                stimulus_asset_ids=grouped_case.stimulus_asset_ids,
                patch_asset_ids=grouped_case.patch_asset_ids,
                questions=generated_questions,
            )
        )
    return preview_cases


def _collect_case_asset_ids(cases: list[BulkRecipeCasePreview]) -> list[str]:
    asset_ids: list[str] = []
    for case in cases:
        asset_ids.extend(case.stimulus_asset_ids)
        asset_ids.extend(case.patch_asset_ids)
    return [item for item in asset_ids if item]


def _persist_bulk_questions(
    db: Session,
    *,
    version: QuestionnaireVersion,
    preview_cases: list[BulkRecipeCasePreview],
    replace_existing_questions: bool,
) -> int:
    if replace_existing_questions:
        db.execute(delete(Question).where(Question.questionnaire_version_id == version.id))

    next_position = (
        db.scalar(
            select(func.max(Question.position)).where(Question.questionnaire_version_id == version.id)
        )
        or 0
    )

    created_questions = 0
    for case in preview_cases:
        for generated in case.questions:
            _validate_generated_question(generated)
            next_position += 1
            question = Question(
                questionnaire_version_id=version.id,
                position=next_position,
                prompt_text=generated.prompt_text.strip(),
                question_type=generated.question_type,
                is_required=generated.is_required,
                config_json=json.dumps(generated.config),
            )
            db.add(question)
            db.flush()
            for choice_index, choice in enumerate(generated.choices, start=1):
                db.add(
                    Choice(
                        question_id=question.id,
                        position=choice_index,
                        label=choice.label,
                        value=choice.value,
                    )
                )
            created_questions += 1

    version.updated_at = utcnow()
    return created_questions


@router.get("", response_model=list[QuestionnaireSummaryOut])
def list_questionnaires(
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> list[QuestionnaireSummaryOut]:
    stmt = select(Questionnaire).order_by(desc(Questionnaire.updated_at)).limit(200)
    if not _is_superadmin(current_user):
        stmt = stmt.where(Questionnaire.owner_admin_id == current_user.id)

    questionnaires = db.execute(stmt).scalars().all()
    return [_to_questionnaire_summary_out(item) for item in questionnaires]


@router.post("", response_model=QuestionnaireSummaryOut)
def create_questionnaire(
    payload: QuestionnaireCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireSummaryOut:
    candidate_source = payload.slug if payload.slug else payload.title
    unique_slug = _build_unique_slug(db, requested_slug=candidate_source)

    questionnaire = Questionnaire(
        owner_admin_id=current_user.id,
        slug=unique_slug,
        title=payload.title.strip(),
        description=payload.description,
    )
    db.add(questionnaire)
    db.flush()

    version = QuestionnaireVersion(
        questionnaire_id=questionnaire.id,
        version_number=1,
        status=QuestionnaireVersionStatus.DRAFT,
        instructions_markdown=payload.instructions_markdown,
        consent_text=normalize_optional_consent_text(payload.consent_text),
        created_by_id=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(questionnaire)
    return _to_questionnaire_summary_out(questionnaire)


@router.get("/{questionnaire_id}", response_model=QuestionnaireDetailOut)
def get_questionnaire(
    questionnaire_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireDetailOut:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    versions = (
        db.execute(
            select(QuestionnaireVersion)
            .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
            .order_by(desc(QuestionnaireVersion.version_number))
        )
        .scalars()
        .all()
    )
    return QuestionnaireDetailOut(
        id=questionnaire.id,
        owner_admin_username=questionnaire.owner_admin.username,
        slug=questionnaire.slug,
        title=questionnaire.title,
        description=questionnaire.description,
        is_archived=questionnaire.is_archived,
        created_at=questionnaire.created_at,
        updated_at=questionnaire.updated_at,
        versions=[_to_version_summary_out(version) for version in versions],
    )


@router.get("/{questionnaire_id}/responses", response_model=list[AdminResponseSummaryOut])
def list_questionnaire_responses(
    questionnaire_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
    version_id: str | None = Query(default=None),
    username: str | None = Query(default=None),
) -> list[AdminResponseSummaryOut]:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    stmt = (
        select(Response, User, QuestionnaireVersion)
        .join(User, User.id == Response.user_id)
        .join(QuestionnaireVersion, QuestionnaireVersion.id == Response.questionnaire_version_id)
        .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
        .order_by(desc(Response.submitted_at))
        .limit(1000)
    )
    if version_id:
        stmt = stmt.where(Response.questionnaire_version_id == version_id)
    if username:
        stmt = stmt.where(User.username == normalize_username(username))

    rows = db.execute(stmt).all()
    if not rows:
        return []

    response_ids = [response.id for response, _, _ in rows]
    count_rows = db.execute(
        select(ResponseItem.response_id, func.count(ResponseItem.id))
        .where(ResponseItem.response_id.in_(response_ids))
        .group_by(ResponseItem.response_id)
    ).all()
    answer_count_by_response_id = {
        response_id: int(answer_count)
        for response_id, answer_count in count_rows
    }

    return [
        AdminResponseSummaryOut(
            response_id=response.id,
            questionnaire_id=questionnaire.id,
            questionnaire_version_id=version.id,
            questionnaire_version_number=version.version_number,
            username=user.username,
            user_role=user.role,
            submitted_at=response.submitted_at,
            answer_count=answer_count_by_response_id.get(response.id, 0),
        )
        for response, user, version in rows
    ]


@router.get("/{questionnaire_id}/responses/export.csv")
def export_questionnaire_responses_csv(
    questionnaire_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
    version_id: str | None = Query(default=None),
) -> FastAPIResponse:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )

    selected_version = (
        db.scalar(
            select(QuestionnaireVersion)
            .where(QuestionnaireVersion.id == version_id)
            .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
        )
        if version_id
        else db.scalar(
            select(QuestionnaireVersion)
            .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
            .order_by(desc(QuestionnaireVersion.version_number))
        )
    )
    if not selected_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Questionnaire version not found",
        )

    ordered_questions = db.execute(
        select(Question)
        .where(Question.questionnaire_version_id == selected_version.id)
        .order_by(Question.position)
    ).scalars().all()
    case_index_by_key: dict[str, int] = {}
    case_question_count_by_key: dict[str, int] = {}
    case_question_identifier_by_question_id: dict[str, str] = {}
    for question in ordered_questions:
        config = _parse_json_object(question.config_json)
        raw_case_key = str(config.get("case_key") or "").strip()
        case_key = raw_case_key or f"question-{question.id}"
        if case_key not in case_index_by_key:
            case_index_by_key[case_key] = len(case_index_by_key) + 1
            case_question_count_by_key[case_key] = 0
        case_question_count_by_key[case_key] += 1
        case_question_identifier_by_question_id[question.id] = (
            f"c{case_index_by_key[case_key]}q{case_question_count_by_key[case_key]}"
        )

    item_rows = db.execute(
        select(ResponseItem, Response, User, Question)
        .join(Response, Response.id == ResponseItem.response_id)
        .join(User, User.id == Response.user_id)
        .join(Question, Question.id == ResponseItem.question_id)
        .where(Response.questionnaire_version_id == selected_version.id)
        .where(Question.questionnaire_version_id == selected_version.id)
        .order_by(desc(Response.submitted_at), Response.id, Question.position)
    ).all()

    question_config_by_id: dict[str, dict] = {}
    stimulus_asset_ids: set[str] = set()
    for _, _, _, question in item_rows:
        if question.id in question_config_by_id:
            continue
        config = _parse_json_object(question.config_json)
        question_config_by_id[question.id] = config
        raw_stimulus_ids = config.get("stimulus_asset_ids", [])
        if not isinstance(raw_stimulus_ids, list):
            continue
        for raw_asset_id in raw_stimulus_ids:
            if not isinstance(raw_asset_id, str):
                continue
            asset_id = raw_asset_id.strip()
            if asset_id:
                stimulus_asset_ids.add(asset_id)

    asset_filename_by_id: dict[str, str] = {}
    if stimulus_asset_ids:
        asset_rows = db.execute(
            select(Asset.id, Asset.original_path, Asset.file_name)
            .where(Asset.id.in_(stimulus_asset_ids))
        ).all()
        for asset_id, original_path, file_name in asset_rows:
            preferred_name = (original_path or "").strip() or (file_name or "").strip()
            if preferred_name:
                asset_filename_by_id[asset_id] = preferred_name

    fieldnames = [
        "response_id",
        "questionnaire_id",
        "questionnaire_title",
        "questionnaire_version_id",
        "questionnaire_version_number",
        "username",
        "user_role",
        "submitted_at",
        "case_key",
        "question_identifier",
        "case_image_filenames",
        "question_id",
        "question_position",
        "question_prompt_text",
        "question_type",
        "answer_value",
    ]

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()

    for item, response, user, question in item_rows:
        config = question_config_by_id.get(question.id) or _parse_json_object(question.config_json)
        case_key = str(config.get("case_key") or "").strip()
        raw_stimulus_ids = config.get("stimulus_asset_ids", [])
        case_filenames: list[str] = []
        seen_case_filenames: set[str] = set()
        if isinstance(raw_stimulus_ids, list):
            for raw_asset_id in raw_stimulus_ids:
                if not isinstance(raw_asset_id, str):
                    continue
                asset_id = raw_asset_id.strip()
                if not asset_id:
                    continue
                filename = asset_filename_by_id.get(asset_id, asset_id)
                if filename in seen_case_filenames:
                    continue
                seen_case_filenames.add(filename)
                case_filenames.append(filename)

        parsed_value = _parse_json_any(item.answer_json)
        writer.writerow(
            {
                "response_id": response.id,
                "questionnaire_id": questionnaire.id,
                "questionnaire_title": questionnaire.title,
                "questionnaire_version_id": selected_version.id,
                "questionnaire_version_number": selected_version.version_number,
                "username": user.username,
                "user_role": user.role.value,
                "submitted_at": response.submitted_at.isoformat(),
                "case_key": case_key,
                "question_identifier": case_question_identifier_by_question_id.get(question.id, ""),
                "case_image_filenames": " | ".join(case_filenames),
                "question_id": question.id,
                "question_position": question.position,
                "question_prompt_text": question.prompt_text,
                "question_type": question.question_type.value,
                "answer_value": _stringify_answer_value(parsed_value),
            }
        )

    filename = f"questionnaire-{questionnaire.id}-v{selected_version.version_number}-responses.csv"

    return FastAPIResponse(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{questionnaire_id}/responses/{response_id}",
    response_model=AdminResponseDetailOut,
)
def get_questionnaire_response_detail(
    questionnaire_id: str,
    response_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> AdminResponseDetailOut:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    response_row = db.execute(
        select(Response, User, QuestionnaireVersion)
        .join(User, User.id == Response.user_id)
        .join(QuestionnaireVersion, QuestionnaireVersion.id == Response.questionnaire_version_id)
        .where(Response.id == response_id)
        .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
    ).first()
    if not response_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Response not found")
    response, user, version = response_row

    item_rows = db.execute(
        select(ResponseItem, Question)
        .join(Question, Question.id == ResponseItem.question_id)
        .where(ResponseItem.response_id == response.id)
        .order_by(Question.position)
    ).all()
    items: list[AdminResponseItemOut] = [
        AdminResponseItemOut(
            question_id=question.id,
            question_position=question.position,
            question_prompt_text=question.prompt_text,
            question_type=question.question_type,
            answer_value=_parse_json_any(item.answer_json),
        )
        for item, question in item_rows
    ]

    return AdminResponseDetailOut(
        response_id=response.id,
        questionnaire_id=questionnaire.id,
        questionnaire_version_id=version.id,
        questionnaire_version_number=version.version_number,
        username=user.username,
        user_role=user.role,
        submitted_at=response.submitted_at,
        items=items,
    )


@router.patch("/{questionnaire_id}", response_model=QuestionnaireSummaryOut)
def update_questionnaire(
    questionnaire_id: str,
    payload: QuestionnaireUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireSummaryOut:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    questionnaire.title = payload.title.strip()
    questionnaire.description = payload.description
    if payload.slug is not None:
        questionnaire.slug = _build_unique_slug(
            db,
            requested_slug=payload.slug,
            exclude_questionnaire_id=questionnaire.id,
        )
    questionnaire.updated_at = utcnow()
    db.commit()
    db.refresh(questionnaire)
    return _to_questionnaire_summary_out(questionnaire)


@router.delete("/{questionnaire_id}")
def delete_questionnaire(
    questionnaire_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )

    version_ids = (
        db.execute(
            select(QuestionnaireVersion.id).where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
        )
        .scalars()
        .all()
    )
    if version_ids:
        db.execute(
            delete(UserAssignment).where(UserAssignment.questionnaire_version_id.in_(version_ids))
        )
        db.execute(
            delete(QuestionnaireConsent).where(QuestionnaireConsent.questionnaire_version_id.in_(version_ids))
        )

        response_ids = (
            db.execute(
                select(Response.id).where(Response.questionnaire_version_id.in_(version_ids))
            )
            .scalars()
            .all()
        )
        if response_ids:
            db.execute(delete(ResponseItem).where(ResponseItem.response_id.in_(response_ids)))
        db.execute(delete(Response).where(Response.questionnaire_version_id.in_(version_ids)))

        question_ids = (
            db.execute(
                select(Question.id).where(Question.questionnaire_version_id.in_(version_ids))
            )
            .scalars()
            .all()
        )
        if question_ids:
            db.execute(delete(Choice).where(Choice.question_id.in_(question_ids)))
        db.execute(delete(Question).where(Question.questionnaire_version_id.in_(version_ids)))

        db.execute(delete(QuestionnaireVersion).where(QuestionnaireVersion.id.in_(version_ids)))

    db.execute(delete(Asset).where(Asset.questionnaire_id == questionnaire.id))
    db.delete(questionnaire)
    db.commit()
    return {"ok": True}


@router.post("/{questionnaire_id}/versions", response_model=QuestionnaireVersionSummaryOut)
def create_questionnaire_version(
    questionnaire_id: str,
    payload: QuestionnaireVersionCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireVersionSummaryOut:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    existing_draft = db.scalar(
        select(QuestionnaireVersion)
        .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
        .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.DRAFT)
    )
    if existing_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This questionnaire already has a draft version",
        )

    latest_version_number = (
        db.scalar(
            select(func.max(QuestionnaireVersion.version_number)).where(
                QuestionnaireVersion.questionnaire_id == questionnaire.id
            )
        )
        or 0
    )

    source_version = db.scalar(
        select(QuestionnaireVersion)
        .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
        .order_by(desc(QuestionnaireVersion.version_number))
        .limit(1)
    )

    draft = QuestionnaireVersion(
        questionnaire_id=questionnaire.id,
        version_number=int(latest_version_number) + 1,
        status=QuestionnaireVersionStatus.DRAFT,
        instructions_markdown=(
            payload.instructions_markdown
            if payload.instructions_markdown
            else (source_version.instructions_markdown if source_version else "")
        ),
        consent_text=(
            normalize_optional_consent_text(payload.consent_text)
            if "consent_text" in payload.model_fields_set
            else (source_version.consent_text if source_version else None)
        ),
        created_by_id=current_user.id,
    )
    db.add(draft)
    db.flush()

    if source_version:
        source_questions = (
            db.execute(
                select(Question)
                .where(Question.questionnaire_version_id == source_version.id)
                .order_by(Question.position)
            )
            .scalars()
            .all()
        )
        for source_question in source_questions:
            cloned_question = Question(
                questionnaire_version_id=draft.id,
                position=source_question.position,
                prompt_text=source_question.prompt_text,
                question_type=source_question.question_type,
                is_required=source_question.is_required,
                config_json=source_question.config_json,
            )
            db.add(cloned_question)
            db.flush()

            source_choices = (
                db.execute(
                    select(Choice)
                    .where(Choice.question_id == source_question.id)
                    .order_by(Choice.position)
                )
                .scalars()
                .all()
            )
            for source_choice in source_choices:
                db.add(
                    Choice(
                        question_id=cloned_question.id,
                        position=source_choice.position,
                        label=source_choice.label,
                        value=source_choice.value,
                    )
                )

    db.commit()
    db.refresh(draft)
    return _to_version_summary_out(draft)


@router.get(
    "/{questionnaire_id}/versions/{version_id}",
    response_model=QuestionnaireVersionDetailOut,
)
def get_questionnaire_version(
    questionnaire_id: str,
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireVersionDetailOut:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    return _to_version_detail_out(version)


@router.patch(
    "/{questionnaire_id}/versions/{version_id}",
    response_model=QuestionnaireVersionSummaryOut,
)
def update_questionnaire_version(
    questionnaire_id: str,
    version_id: str,
    payload: QuestionnaireVersionUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireVersionSummaryOut:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)
    version.instructions_markdown = payload.instructions_markdown
    if "consent_text" in payload.model_fields_set:
        version.consent_text = normalize_optional_consent_text(payload.consent_text)
    version.updated_at = utcnow()
    db.commit()
    db.refresh(version)
    return _to_version_summary_out(version)


@router.post(
    "/{questionnaire_id}/versions/{version_id}/questions",
    response_model=QuestionOut,
)
def create_question(
    questionnaire_id: str,
    version_id: str,
    payload: QuestionCreate,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionOut:
    _validate_question_payload(payload)
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    duplicate_position = db.scalar(
        select(Question.id)
        .where(Question.questionnaire_version_id == version.id)
        .where(Question.position == payload.position)
    )
    if duplicate_position:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question position already exists in this version",
        )

    question = Question(
        questionnaire_version_id=version.id,
        position=payload.position,
        prompt_text=payload.prompt_text,
        question_type=payload.question_type,
        is_required=payload.is_required,
        config_json=json.dumps(payload.config),
    )
    db.add(question)
    db.flush()
    for choice in payload.choices:
        db.add(
            Choice(
                question_id=question.id,
                position=choice.position,
                label=choice.label,
                value=choice.value,
            )
        )
    version.updated_at = utcnow()
    db.commit()
    db.refresh(question)
    return _to_question_out(question)


@router.put(
    "/{questionnaire_id}/versions/{version_id}/questions/{question_id}",
    response_model=QuestionOut,
)
def update_question(
    questionnaire_id: str,
    version_id: str,
    question_id: str,
    payload: QuestionUpdate,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionOut:
    _validate_question_payload(payload)
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        .where(Question.questionnaire_version_id == version.id)
    )
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    duplicate_position = db.scalar(
        select(Question.id)
        .where(Question.questionnaire_version_id == version.id)
        .where(Question.position == payload.position)
        .where(Question.id != question.id)
    )
    if duplicate_position:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question position already exists in this version",
        )

    question.position = payload.position
    question.prompt_text = payload.prompt_text
    question.question_type = payload.question_type
    question.is_required = payload.is_required
    question.config_json = json.dumps(payload.config)
    question.updated_at = utcnow()

    db.execute(delete(Choice).where(Choice.question_id == question.id))
    db.flush()

    for choice in payload.choices:
        db.add(
            Choice(
                question_id=question.id,
                position=choice.position,
                label=choice.label,
                value=choice.value,
            )
        )

    version.updated_at = utcnow()
    db.commit()
    db.refresh(question)
    return _to_question_out(question)


@router.delete("/{questionnaire_id}/versions/{version_id}/questions/{question_id}")
def delete_question(
    questionnaire_id: str,
    version_id: str,
    question_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    question = db.scalar(
        select(Question)
        .where(Question.id == question_id)
        .where(Question.questionnaire_version_id == version.id)
    )
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    db.delete(question)
    version.updated_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post(
    "/{questionnaire_id}/versions/{version_id}/publish",
    response_model=QuestionnaireVersionSummaryOut,
)
def publish_questionnaire_version(
    questionnaire_id: str,
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> QuestionnaireVersionSummaryOut:
    questionnaire = _get_accessible_questionnaire(
        db, questionnaire_id=questionnaire_id, user=current_user
    )
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    currently_published = (
        db.execute(
            select(QuestionnaireVersion)
            .where(QuestionnaireVersion.questionnaire_id == questionnaire.id)
            .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
        )
        .scalars()
        .all()
    )
    for published_version in currently_published:
        published_version.status = QuestionnaireVersionStatus.ARCHIVED
        published_version.updated_at = utcnow()

    version.status = QuestionnaireVersionStatus.PUBLISHED
    version.published_at = utcnow()
    version.updated_at = utcnow()
    questionnaire.updated_at = utcnow()
    db.commit()
    db.refresh(version)
    return _to_version_summary_out(version)


@router.post(
    "/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview",
    response_model=BulkRecipePreviewResponse,
)
def preview_bulk_recipe(
    questionnaire_id: str,
    version_id: str,
    payload: BulkRecipePreviewRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> BulkRecipePreviewResponse:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    if not payload.question_templates and payload.patch_question_template is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one question template or patch_question_template is required",
        )

    assets = _get_accessible_assets(
        db,
        asset_ids=payload.asset_ids,
        user=current_user,
        questionnaire_id=questionnaire_id,
    )
    grouping_result = group_assets_for_recipe(
        recipe_type=payload.recipe_type,
        assets=assets,
        recipe_config=payload.recipe_config,
    )
    preview_cases = _build_bulk_preview(
        grouped_cases=grouping_result.cases,
        question_templates=payload.question_templates,
        patch_question_template=payload.patch_question_template,
        recipe_type=payload.recipe_type.value,
        recipe_config=payload.recipe_config,
    )
    return BulkRecipePreviewResponse(cases=preview_cases, warnings=grouping_result.warnings)


@router.post(
    "/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply",
    response_model=BulkRecipeApplyResponse,
)
def apply_bulk_recipe(
    questionnaire_id: str,
    version_id: str,
    payload: BulkRecipeApplyRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> BulkRecipeApplyResponse:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    if not payload.question_templates and payload.patch_question_template is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one question template or patch_question_template is required",
        )

    assets = _get_accessible_assets(
        db,
        asset_ids=payload.asset_ids,
        user=current_user,
        questionnaire_id=questionnaire_id,
    )
    grouping_result = group_assets_for_recipe(
        recipe_type=payload.recipe_type,
        assets=assets,
        recipe_config=payload.recipe_config,
    )
    preview_cases = _build_bulk_preview(
        grouped_cases=grouping_result.cases,
        question_templates=payload.question_templates,
        patch_question_template=payload.patch_question_template,
        recipe_type=payload.recipe_type.value,
        recipe_config=payload.recipe_config,
    )

    if not preview_cases:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No cases were generated from the provided recipe and assets",
        )

    created_questions = _persist_bulk_questions(
        db,
        version=version,
        preview_cases=preview_cases,
        replace_existing_questions=payload.replace_existing_questions,
    )
    db.commit()
    return BulkRecipeApplyResponse(
        cases=len(preview_cases),
        created_questions=created_questions,
        warnings=grouping_result.warnings,
    )


@router.post(
    "/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply-preview",
    response_model=BulkRecipeApplyResponse,
)
def apply_bulk_recipe_preview(
    questionnaire_id: str,
    version_id: str,
    payload: BulkRecipeApplyPreviewRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
) -> BulkRecipeApplyResponse:
    _get_accessible_questionnaire(db, questionnaire_id=questionnaire_id, user=current_user)
    version = _get_version_for_questionnaire(db, questionnaire_id=questionnaire_id, version_id=version_id)
    _ensure_draft(version)

    total_questions = sum(len(case.questions) for case in payload.cases)
    if total_questions == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cases must include at least one generated question",
        )

    referenced_asset_ids = _collect_case_asset_ids(payload.cases)
    if referenced_asset_ids:
        _get_accessible_assets(
            db,
            asset_ids=referenced_asset_ids,
            user=current_user,
            questionnaire_id=questionnaire_id,
        )

    created_questions = _persist_bulk_questions(
        db,
        version=version,
        preview_cases=payload.cases,
        replace_existing_questions=payload.replace_existing_questions,
    )
    db.commit()
    return BulkRecipeApplyResponse(
        cases=len(payload.cases),
        created_questions=created_questions,
        warnings=[],
    )
