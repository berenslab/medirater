import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import (
    Question,
    Questionnaire,
    QuestionnaireConsent,
    QuestionnaireVersion,
    QuestionnaireVersionStatus,
    Role,
    UserAssignment,
)
from app.services.bulk_recipes.base import normalize_recipe_type
from app.services.session_service import get_user_by_session_token

router = APIRouter(include_in_schema=False)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def _current_user_from_session(request: Request, db: Session, settings: Settings):
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        return None
    return get_user_by_session_token(db, session_token=session_token)


def _landing_url_for(user) -> str:
    if _requires_account_completion(user):
        return "/account"
    if user and user.role in {Role.ADMIN, Role.SUPERADMIN}:
        return "/questionnaires"
    return "/assigned"


def _nav_context(user) -> dict[str, bool]:
    is_admin = bool(user and user.role in {Role.ADMIN, Role.SUPERADMIN})
    return {
        "show_questionnaires_link": is_admin,
        "show_users_link": is_admin,
        "show_assigned_link": bool(user and user.role in {Role.USER, Role.ADMIN}),
        "show_account_link": bool(user),
        "show_settings_link": False,
    }


def _has_user_assignment_for_published_version(
    db: Session,
    *,
    user,
    questionnaire_version_id: str,
) -> bool:
    version_row = db.execute(
        select(QuestionnaireVersion, Questionnaire)
        .join(Questionnaire, Questionnaire.id == QuestionnaireVersion.questionnaire_id)
        .where(QuestionnaireVersion.id == questionnaire_version_id)
        .where(QuestionnaireVersion.status == QuestionnaireVersionStatus.PUBLISHED)
    ).first()
    if not version_row:
        return False
    _, questionnaire = version_row

    if user.role == Role.SUPERADMIN:
        return True

    if user.role == Role.ADMIN and questionnaire.owner_admin_id == user.id:
        return True

    assignment = db.scalar(
        select(UserAssignment)
        .where(UserAssignment.user_id == user.id)
        .where(UserAssignment.questionnaire_version_id == questionnaire_version_id)
        .where(UserAssignment.is_active.is_(True))
    )
    return bool(assignment)


def _has_user_consent_for_version(
    db: Session,
    *,
    user_id: str,
    questionnaire_version_id: str,
) -> bool:
    consent = db.scalar(
        select(QuestionnaireConsent)
        .where(QuestionnaireConsent.user_id == user_id)
        .where(QuestionnaireConsent.questionnaire_version_id == questionnaire_version_id)
    )
    return bool(consent)


def _parse_json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _requires_account_completion(user) -> bool:
    return bool(user and user.role == Role.USER and user.year_of_experience is None)


def _redirect_if_account_completion_required(user, *, current_path: str) -> RedirectResponse | None:
    if not _requires_account_completion(user):
        return None
    if current_path == "/account":
        return None
    return RedirectResponse(url="/account", status_code=303)


def _resolve_answer_template_for_version(db: Session, *, questionnaire_version_id: str) -> tuple[str, str | None]:
    config_rows = db.execute(
        select(Question.config_json)
        .where(Question.questionnaire_version_id == questionnaire_version_id)
        .order_by(Question.position)
    ).scalars().all()

    detected_recipe_type: str | None = None
    for config_json in config_rows:
        config = _parse_json_object(config_json)
        recipe_type = normalize_recipe_type(config.get("recipe_type"))
        if not recipe_type:
            continue

        if detected_recipe_type is None:
            detected_recipe_type = recipe_type

        template_name = f"recipes/{recipe_type}/answer.html"
        try:
            templates.env.get_template(template_name)
            return template_name, recipe_type
        except TemplateNotFound:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Missing answer template for recipe '{recipe_type}'",
            )

    default_template = "recipes/default/answer.html"
    try:
        templates.env.get_template(default_template)
    except TemplateNotFound:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Missing default answer template",
        )
    return default_template, detected_recipe_type


@router.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    user = _current_user_from_session(request, db, settings)
    if user:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)
    return RedirectResponse(url="/login", status_code=307)


@router.get("/signup")
def signup_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if user:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={
            "title": "Signup",
            "heading": "Signup",
            "token_required": False,
            "mode_aware": True,
            "links": [{"href": "/login", "label": "Back to login"}],
        },
    )


@router.get("/admin_signup")
def admin_signup_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if user:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={
            "title": "Admin Signup",
            "heading": "Admin or Superadmin signup",
            "token_required": True,
            "token_note": "Admin signup always requires a privileged one-time token.",
            "mode_aware": False,
            "links": [],
        },
    )


@router.get("/login")
def login_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if user:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "Login",
            "heading": "Login",
        },
    )


@router.get("/questionnaires")
def questionnaires_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(user, current_path="/questionnaires")
    if redirect:
        return redirect

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="questionnaires.html",
        context={
            **_nav_context(user),
        },
    )


@router.get("/questionnaires/{questionnaire_id}/design")
def questionnaire_design_page(
    questionnaire_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(
        user,
        current_path=f"/questionnaires/{questionnaire_id}/design",
    )
    if redirect:
        return redirect

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    questionnaire = db.get(Questionnaire, questionnaire_id)
    if not questionnaire:
        return RedirectResponse(url="/questionnaires", status_code=303)

    if user.role != Role.SUPERADMIN and questionnaire.owner_admin_id != user.id:
        return RedirectResponse(url="/questionnaires", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="questionnaire_design.html",
        context={
            **_nav_context(user),
            "questionnaire_id": questionnaire_id,
        },
    )


@router.get("/questionnaires/{questionnaire_id}/responses")
def questionnaire_responses_page(
    questionnaire_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(
        user,
        current_path=f"/questionnaires/{questionnaire_id}/responses",
    )
    if redirect:
        return redirect

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    questionnaire = db.get(Questionnaire, questionnaire_id)
    if not questionnaire:
        return RedirectResponse(url="/questionnaires", status_code=303)

    if user.role != Role.SUPERADMIN and questionnaire.owner_admin_id != user.id:
        return RedirectResponse(url="/questionnaires", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="questionnaire_responses.html",
        context={
            **_nav_context(user),
            "questionnaire_id": questionnaire_id,
        },
    )


@router.get("/questionnaires/{questionnaire_id}/assignments")
def questionnaire_assignments_page(
    questionnaire_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(
        user,
        current_path=f"/questionnaires/{questionnaire_id}/assignments",
    )
    if redirect:
        return redirect

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    questionnaire = db.get(Questionnaire, questionnaire_id)
    if not questionnaire:
        return RedirectResponse(url="/questionnaires", status_code=303)

    if user.role != Role.SUPERADMIN and questionnaire.owner_admin_id != user.id:
        return RedirectResponse(url="/questionnaires", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="questionnaire_assignments.html",
        context={
            **_nav_context(user),
            "questionnaire_id": questionnaire_id,
        },
    )


@router.get("/assigned")
def assigned_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(user, current_path="/assigned")
    if redirect:
        return redirect

    return templates.TemplateResponse(
        request=request,
        name="assigned.html",
        context={
            **_nav_context(user),
        },
    )


@router.get("/answer/{questionnaire_version_id}")
def answer_page(
    questionnaire_version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(
        user,
        current_path=f"/answer/{questionnaire_version_id}",
    )
    if redirect:
        return redirect

    if not _has_user_assignment_for_published_version(
        db,
        user=user,
        questionnaire_version_id=questionnaire_version_id,
    ):
        return RedirectResponse(url="/assigned", status_code=303)

    if not _has_user_consent_for_version(
        db,
        user_id=user.id,
        questionnaire_version_id=questionnaire_version_id,
    ):
        return RedirectResponse(url=f"/answer/{questionnaire_version_id}/consent", status_code=303)

    answer_template_name, detected_recipe_type = _resolve_answer_template_for_version(
        db,
        questionnaire_version_id=questionnaire_version_id,
    )

    return templates.TemplateResponse(
        request=request,
        name=answer_template_name,
        context={
            **_nav_context(user),
            "questionnaire_version_id": questionnaire_version_id,
            "answer_layout_mode": "auto",
            "detected_recipe_type": detected_recipe_type,
        },
    )


@router.get("/answer/{questionnaire_version_id}/consent")
def answer_consent_page(
    questionnaire_version_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(
        user,
        current_path=f"/answer/{questionnaire_version_id}/consent",
    )
    if redirect:
        return redirect

    if not _has_user_assignment_for_published_version(
        db,
        user=user,
        questionnaire_version_id=questionnaire_version_id,
    ):
        return RedirectResponse(url="/assigned", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="answer_consent.html",
        context={
            **_nav_context(user),
            "questionnaire_version_id": questionnaire_version_id,
        },
    )


@router.get("/users")
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    redirect = _redirect_if_account_completion_required(user, current_path="/users")
    if redirect:
        return redirect

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            **_nav_context(user),
        },
    )


@router.get("/account")
def account_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            **_nav_context(user),
        },
    )


@router.get("/settings")
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.role in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url="/users", status_code=303)
    return RedirectResponse(url="/account", status_code=303)


@router.get("/admin")
def admin_page() -> RedirectResponse:
    return RedirectResponse(url="/questionnaires", status_code=303)


@router.get("/passkeys")
def passkeys_page() -> RedirectResponse:
    return RedirectResponse(url="/account", status_code=303)


@router.get("/me")
def me_page() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=303)
