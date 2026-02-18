from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Questionnaire, Role
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
    if user and user.role in {Role.ADMIN, Role.SUPERADMIN}:
        return "/questionnaires"
    return "/settings"


def _nav_context(user) -> dict[str, bool]:
    is_admin = bool(user and user.role in {Role.ADMIN, Role.SUPERADMIN})
    return {
        "show_questionnaires_link": is_admin,
        "show_users_link": bool(user and user.role == Role.SUPERADMIN),
        "show_settings_link": bool(user),
    }


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

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url="/settings", status_code=303)

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

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url="/settings", status_code=303)

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


@router.get("/users")
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if user.role != Role.SUPERADMIN:
        return RedirectResponse(url=_landing_url_for(user), status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="users.html",
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

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            **_nav_context(user),
        },
    )


@router.get("/admin")
def admin_page() -> RedirectResponse:
    return RedirectResponse(url="/questionnaires", status_code=303)


@router.get("/passkeys")
def passkeys_page() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/me")
def me_page() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=303)
