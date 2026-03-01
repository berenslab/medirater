import json
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Asset,
    Choice,
    Question,
    QuestionType,
    QuestionnaireConsent,
    Questionnaire,
    QuestionnaireVersion,
    QuestionnaireVersionStatus,
    Role,
    Response,
    ResponseDraft,
    ResponseDraftItem,
    ResponseItem,
    User,
    UserAssignment,
    utcnow,
)
from app.schemas import (
    AssignedQuestionnaireOut,
    ChoiceOut,
    ExistingAnswerOut,
    QuestionnaireConsentOut,
    QuestionnaireConsentRequest,
    QuestionOut,
    QuestionnaireForAnswerOut,
    SaveQuestionnaireDraftRequest,
    SaveQuestionnaireDraftResponse,
    SubmitQuestionnaireRequest,
    SubmitQuestionnaireResponse,
)
from app.services.consent_service import resolve_effective_consent_text

router = APIRouter(prefix="/api/user", tags=["user_questionnaires"])

_SAFE_INLINE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "application/pdf",
    }
)


def _safe_media_type(mime_type: str | None, file_name: str) -> tuple[str, str]:
    """Return (media_type, content_disposition) that is safe for inline serving."""
    safe_name = file_name.replace('"', "_") if file_name else "download"
    if mime_type and mime_type.lower() in _SAFE_INLINE_TYPES:
        return mime_type, f'inline; filename="{safe_name}"'
    return "application/octet-stream", f'attachment; filename="{safe_name}"'


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


def _to_choice_out(choice: Choice) -> ChoiceOut:
    return ChoiceOut.model_validate(choice)


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


def _get_accessible_published_version(
    db: Session,
    *,
    user: User,
    questionnaire_version_id: str,
) -> QuestionnaireVersion:
    version = db.scalar(
        select(QuestionnaireVersion)
        .options(
            selectinload(QuestionnaireVersion.questionnaire),
            selectinload(QuestionnaireVersion.questions).selectinload(Question.choices),
        )
        .where(QuestionnaireVersion.id == questionnaire_version_id)
        .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
    )
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire version not found")

    if user.role == Role.SUPERADMIN:
        return version

    questionnaire = version.questionnaire
    if user.role == Role.ADMIN and questionnaire.owner_admin_id == user.id:
        return version

    assignment = db.scalar(
        select(UserAssignment)
        .where(UserAssignment.user_id == user.id)
        .where(UserAssignment.questionnaire_version_id == questionnaire_version_id)
        .where(UserAssignment.is_active.is_(True))
    )
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire access not granted")

    return version


def _normalize_answer_value(question: Question, raw_value: Any) -> Any:
    allowed_values = {choice.value for choice in question.choices}

    if question.question_type == QuestionType.SINGLE_CHOICE:
        if not isinstance(raw_value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {question.id} expects a single string choice",
            )
        value = raw_value.strip()
        if value not in allowed_values:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {question.id} has invalid choice value '{value}'",
            )
        return value

    if question.question_type == QuestionType.MULTI_CHOICE:
        if not isinstance(raw_value, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {question.id} expects a list of choice values",
            )

        values: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            if not isinstance(item, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} expects string choice values",
                )
            value = item.strip()
            if not value:
                continue
            if value in seen:
                continue
            if value not in allowed_values:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} has invalid choice value '{value}'",
                )
            seen.add(value)
            values.append(value)
        return values

    if question.question_type == QuestionType.ANNOTATION:
        if not isinstance(raw_value, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {question.id} expects an annotation object",
            )

        raw_points = raw_value.get("points")
        if not isinstance(raw_points, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {question.id} expects annotation.points as a list",
            )

        label_to_value: dict[str, str] = {}
        for choice in question.choices:
            label = str(choice.label or "").strip()
            value = str(choice.value or "").strip()
            if label and value:
                label_to_value[label] = value

        allowed_annotation_labels = {item for item in allowed_values if item}
        if not allowed_annotation_labels:
            # Backward-compatible fallback for legacy annotation configs.
            config = _parse_json_object(question.config_json)
            raw_annotation_labels = config.get("annotation_labels")
            if not isinstance(raw_annotation_labels, list):
                recipe_config = config.get("recipe_config")
                if isinstance(recipe_config, dict):
                    raw_annotation_labels = recipe_config.get("annotation_labels")
            allowed_annotation_labels = {
                str(item).strip()
                for item in (raw_annotation_labels or [])
                if isinstance(item, str) and str(item).strip()
            }

        normalized_points: list[dict[str, Any]] = []
        for point_index, point in enumerate(raw_points, start=1):
            if not isinstance(point, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} must be an object",
                )

            label_raw = point.get("label")
            if not isinstance(label_raw, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} label must be a string",
                )
            label = label_raw.strip()
            if not label:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} label is empty",
                )
            # Accept submitted display labels and normalize to choice value.
            label = label_to_value.get(label, label)
            if allowed_annotation_labels and label not in allowed_annotation_labels:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} label '{label}' is not allowed",
                )

            x_raw = point.get("x")
            y_raw = point.get("y")
            if isinstance(x_raw, bool) or not isinstance(x_raw, (int, float)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} x must be a number",
                )
            if isinstance(y_raw, bool) or not isinstance(y_raw, (int, float)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} y must be a number",
                )

            x = float(x_raw)
            y = float(y_raw)
            if not math.isfinite(x):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} x must be finite",
                )
            if not math.isfinite(y):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {question.id} point #{point_index} y must be finite",
                )

            normalized_points.append(
                {
                    "label": label,
                    "x": int(x) if x.is_integer() else x,
                    "y": int(y) if y.is_integer() else y,
                }
            )

        return {"points": normalized_points}

    if not isinstance(raw_value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Question {question.id} expects text answer",
        )
    return raw_value.strip()


def _asset_id_in_question_config(question: Question, asset_id: str) -> bool:
    config = _parse_json_object(question.config_json)
    if config.get("patch_asset_id") == asset_id:
        return True
    stimulus_ids = config.get("stimulus_asset_ids", [])
    if isinstance(stimulus_ids, list) and asset_id in stimulus_ids:
        return True
    return False


def _get_consent_record(
    db: Session,
    *,
    user_id: str,
    questionnaire_version_id: str,
) -> QuestionnaireConsent | None:
    return db.scalar(
        select(QuestionnaireConsent)
        .where(QuestionnaireConsent.user_id == user_id)
        .where(QuestionnaireConsent.questionnaire_version_id == questionnaire_version_id)
    )


def _normalize_answers_for_version(
    version: QuestionnaireVersion,
    raw_answers: list[Any],
) -> tuple[dict[str, Question], dict[str, Any]]:
    question_by_id = {question.id: question for question in version.questions}
    if not question_by_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Questionnaire has no questions",
        )

    answer_by_question: dict[str, Any] = {}
    for item in raw_answers:
        question_id = item.question_id
        if question_id not in question_by_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown question id {question_id}",
            )
        if question_id in answer_by_question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate answer for question id {question_id}",
            )
        answer_by_question[question_id] = _normalize_answer_value(
            question_by_id[question_id],
            item.value,
        )
    return question_by_id, answer_by_question


@router.get("/assigned-questionnaires", response_model=list[AssignedQuestionnaireOut])
def list_assigned_questionnaires(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssignedQuestionnaireOut]:
    assigned_rows = db.execute(
        select(UserAssignment, QuestionnaireVersion, Questionnaire)
        .join(
            QuestionnaireVersion,
            QuestionnaireVersion.id == UserAssignment.questionnaire_version_id,
        )
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .where(UserAssignment.user_id == current_user.id)
        .where(UserAssignment.is_active.is_(True))
        .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
    ).all()

    accessible_rows: list[tuple[QuestionnaireVersion, Questionnaire, UserAssignment | None, Any]] = []
    # tuple: (version, questionnaire, assignment_or_none, assigned_at_or_none)

    if current_user.role == Role.USER:
        for assignment, version, questionnaire in assigned_rows:
            accessible_rows.append((version, questionnaire, assignment, assignment.created_at))
    elif current_user.role == Role.ADMIN:
        owned_rows = db.execute(
            select(QuestionnaireVersion, Questionnaire)
            .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
            .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
            .where(Questionnaire.owner_admin_id == current_user.id)
        ).all()
        by_version_id: dict[str, tuple[QuestionnaireVersion, Any, Any, Any]] = {}
        for version, questionnaire in owned_rows:
            by_version_id[version.id] = (version, questionnaire, None, version.published_at or version.created_at)
        for assignment, version, questionnaire in assigned_rows:
            by_version_id[version.id] = (version, questionnaire, assignment, assignment.created_at)
        accessible_rows = list(by_version_id.values())
    else:
        published_rows = db.execute(
            select(QuestionnaireVersion, Questionnaire)
            .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
            .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
        ).all()
        accessible_rows = [
            (version, questionnaire, None, version.published_at or version.created_at)
            for version, questionnaire in published_rows
        ]

    accessible_rows.sort(key=lambda item: item[3], reverse=True)
    if not accessible_rows:
        return []

    version_ids = [version.id for version, _, _, _ in accessible_rows]

    count_rows = db.execute(
        select(Question.questionnaire_version_id, func.count(Question.id))
        .where(Question.questionnaire_version_id.in_(version_ids))
        .group_by(Question.questionnaire_version_id)
    ).all()
    question_count_by_version = {version_id: int(count) for version_id, count in count_rows}

    submitted_rows = db.execute(
        select(Response.questionnaire_version_id, Response.submitted_at)
        .where(Response.user_id == current_user.id)
        .where(Response.questionnaire_version_id.in_(version_ids))
    ).all()
    submitted_by_version = {version_id: submitted_at for version_id, submitted_at in submitted_rows}

    return [
        AssignedQuestionnaireOut(
            questionnaire_id=questionnaire.id,
            questionnaire_version_id=version.id,
            title=questionnaire.title,
            description=questionnaire.description,
            version_number=version.version_number,
            instructions_markdown=version.instructions_markdown,
            assigned_at=assigned_at,
            question_count=question_count_by_version.get(version.id, 0),
            submitted_at=submitted_by_version.get(version.id),
        )
        for version, questionnaire, _, assigned_at in accessible_rows
    ]


@router.get(
    "/questionnaires/{questionnaire_version_id}",
    response_model=QuestionnaireForAnswerOut,
)
def get_assigned_questionnaire_for_answer(
    questionnaire_version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionnaireForAnswerOut:
    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )

    existing_response = db.scalar(
        select(Response)
        .options(selectinload(Response.items))
        .where(Response.user_id == current_user.id)
        .where(Response.questionnaire_version_id == version.id)
    )
    existing_draft = db.scalar(
        select(ResponseDraft)
        .options(selectinload(ResponseDraft.items))
        .where(ResponseDraft.user_id == current_user.id)
        .where(ResponseDraft.questionnaire_version_id == version.id)
    )

    questions = sorted(version.questions, key=lambda item: item.position)
    questionnaire = version.questionnaire
    existing_answer_by_question_id: dict[str, Any] = {}
    if existing_response:
        for item in existing_response.items:
            existing_answer_by_question_id[item.question_id] = _parse_json_any(item.answer_json)
    if existing_draft:
        for item in existing_draft.items:
            existing_answer_by_question_id[item.question_id] = _parse_json_any(item.answer_json)
    existing_answers = [
        ExistingAnswerOut(question_id=question.id, value=existing_answer_by_question_id[question.id])
        for question in questions
        if question.id in existing_answer_by_question_id
    ]

    return QuestionnaireForAnswerOut(
        questionnaire_id=questionnaire.id,
        questionnaire_version_id=version.id,
        title=questionnaire.title,
        description=questionnaire.description,
        consent_text=resolve_effective_consent_text(version.consent_text),
        version_number=version.version_number,
        instructions_markdown=version.instructions_markdown,
        questions=[_to_question_out(question) for question in questions],
        existing_answers=existing_answers,
    )


@router.get(
    "/questionnaires/{questionnaire_version_id}/consent",
    response_model=QuestionnaireConsentOut,
)
def get_questionnaire_consent_status(
    questionnaire_version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionnaireConsentOut:
    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )
    consent = _get_consent_record(
        db,
        user_id=current_user.id,
        questionnaire_version_id=version.id,
    )
    return QuestionnaireConsentOut(
        questionnaire_version_id=version.id,
        consented=bool(consent),
        consented_at=consent.consented_at if consent else None,
    )


@router.post(
    "/questionnaires/{questionnaire_version_id}/consent",
    response_model=QuestionnaireConsentOut,
)
def record_questionnaire_consent(
    questionnaire_version_id: str,
    payload: QuestionnaireConsentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QuestionnaireConsentOut:
    if payload.consented is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consent must be true to proceed",
        )

    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )
    consent = _get_consent_record(
        db,
        user_id=current_user.id,
        questionnaire_version_id=version.id,
    )
    if not consent:
        consent = QuestionnaireConsent(
            user_id=current_user.id,
            questionnaire_version_id=version.id,
            consented_at=utcnow(),
        )
        db.add(consent)
        db.commit()
        db.refresh(consent)

    return QuestionnaireConsentOut(
        questionnaire_version_id=version.id,
        consented=True,
        consented_at=consent.consented_at,
    )


@router.post(
    "/questionnaires/{questionnaire_version_id}/draft",
    response_model=SaveQuestionnaireDraftResponse,
)
def save_assigned_questionnaire_draft(
    questionnaire_version_id: str,
    payload: SaveQuestionnaireDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SaveQuestionnaireDraftResponse:
    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )
    consent = _get_consent_record(
        db,
        user_id=current_user.id,
        questionnaire_version_id=version.id,
    )
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent is required before saving answers",
        )

    _, answer_by_question = _normalize_answers_for_version(version, payload.answers)
    saved_at = utcnow()
    draft_record = db.scalar(
        select(ResponseDraft)
        .where(ResponseDraft.user_id == current_user.id)
        .where(ResponseDraft.questionnaire_version_id == questionnaire_version_id)
    )

    if not answer_by_question:
        if draft_record:
            db.execute(delete(ResponseDraftItem).where(ResponseDraftItem.response_draft_id == draft_record.id))
            db.execute(delete(ResponseDraft).where(ResponseDraft.id == draft_record.id))
            db.commit()
        return SaveQuestionnaireDraftResponse(
            draft_id=None,
            saved_at=saved_at,
            answer_count=0,
        )

    if not draft_record:
        draft_record = ResponseDraft(
            user_id=current_user.id,
            questionnaire_version_id=questionnaire_version_id,
            saved_at=saved_at,
        )
        db.add(draft_record)
        db.flush()
    else:
        draft_record.saved_at = saved_at
        db.execute(delete(ResponseDraftItem).where(ResponseDraftItem.response_draft_id == draft_record.id))
        db.flush()

    for question in sorted(version.questions, key=lambda item: item.position):
        if question.id not in answer_by_question:
            continue
        db.add(
            ResponseDraftItem(
                response_draft_id=draft_record.id,
                question_id=question.id,
                answer_json=json.dumps(answer_by_question[question.id]),
            )
        )

    db.commit()
    db.refresh(draft_record)
    return SaveQuestionnaireDraftResponse(
        draft_id=draft_record.id,
        saved_at=draft_record.saved_at,
        answer_count=len(answer_by_question),
    )


@router.post(
    "/questionnaires/{questionnaire_version_id}/responses",
    response_model=SubmitQuestionnaireResponse,
)
def submit_assigned_questionnaire_response(
    questionnaire_version_id: str,
    payload: SubmitQuestionnaireRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmitQuestionnaireResponse:
    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )
    consent = _get_consent_record(
        db,
        user_id=current_user.id,
        questionnaire_version_id=version.id,
    )
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent is required before submitting answers",
        )

    question_by_id, answer_by_question = _normalize_answers_for_version(version, payload.answers)

    for question in question_by_id.values():
        if not question.is_required:
            continue
        if question.id not in answer_by_question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required answer for question id {question.id}",
            )

        answer_value = answer_by_question[question.id]
        if question.question_type in {QuestionType.SHORT_TEXT, QuestionType.LONG_TEXT} and not answer_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Required text answer is empty for question id {question.id}",
            )
        if question.question_type == QuestionType.MULTI_CHOICE and len(answer_value) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Required multi-choice answer is empty for question id {question.id}",
            )
        if question.question_type == QuestionType.ANNOTATION:
            points = answer_value.get("points", []) if isinstance(answer_value, dict) else []
            if len(points) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Required annotation is empty for question id {question.id}",
                )

    response_record = db.scalar(
        select(Response)
        .where(Response.user_id == current_user.id)
        .where(Response.questionnaire_version_id == questionnaire_version_id)
    )
    if not response_record:
        response_record = Response(
            user_id=current_user.id,
            questionnaire_version_id=questionnaire_version_id,
            submitted_at=utcnow(),
        )
        db.add(response_record)
        db.flush()
    else:
        response_record.submitted_at = utcnow()
        db.execute(delete(ResponseItem).where(ResponseItem.response_id == response_record.id))
        db.flush()

    for question in sorted(version.questions, key=lambda item: item.position):
        if question.id not in answer_by_question:
            continue
        db.add(
            ResponseItem(
                response_id=response_record.id,
                question_id=question.id,
                answer_json=json.dumps(answer_by_question[question.id]),
            )
        )

    draft_record = db.scalar(
        select(ResponseDraft)
        .where(ResponseDraft.user_id == current_user.id)
        .where(ResponseDraft.questionnaire_version_id == questionnaire_version_id)
    )
    if draft_record:
        db.execute(delete(ResponseDraftItem).where(ResponseDraftItem.response_draft_id == draft_record.id))
        db.execute(delete(ResponseDraft).where(ResponseDraft.id == draft_record.id))

    db.commit()
    db.refresh(response_record)
    return SubmitQuestionnaireResponse(
        response_id=response_record.id,
        submitted_at=response_record.submitted_at,
    )


@router.get("/questionnaires/{questionnaire_version_id}/assets/{asset_id}/content")
def get_assigned_questionnaire_asset_content(
    questionnaire_version_id: str,
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FastAPIResponse:
    version = _get_accessible_published_version(
        db,
        user=current_user,
        questionnaire_version_id=questionnaire_version_id,
    )

    if not any(_asset_id_in_question_config(question, asset_id) for question in version.questions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found in assigned questionnaire")

    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    media_type, disposition = _safe_media_type(asset.mime_type, asset.file_name)
    return FastAPIResponse(
        content=asset.blob_data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
