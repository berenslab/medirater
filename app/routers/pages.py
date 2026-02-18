from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Role
from app.services.session_service import get_user_by_session_token

router = APIRouter(include_in_schema=False)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _current_user_from_session(request: Request, db: Session, settings: Settings):
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        return None
    return get_user_by_session_token(db, session_token=session_token)


def _show_admin_link_for_user(user) -> bool:
    return bool(user and user.role in {Role.ADMIN, Role.SUPERADMIN})


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=307)


@router.get("/signup")
def signup_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if _current_user_from_session(request, db, settings):
        return RedirectResponse(url="/me", status_code=303)

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
    if _current_user_from_session(request, db, settings):
        return RedirectResponse(url="/me", status_code=303)

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
    if _current_user_from_session(request, db, settings):
        return RedirectResponse(url="/me", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "Login",
            "heading": "Login",
        },
    )


@router.get("/passkeys")
def passkeys_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    return templates.TemplateResponse(
        request=request,
        name="passkeys.html",
        context={"show_admin_link": _show_admin_link_for_user(user)},
    )


@router.get("/admin")
def admin_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if user.role not in {Role.ADMIN, Role.SUPERADMIN}:
        return RedirectResponse(url="/me", status_code=303)

    return templates.TemplateResponse(
        request=request, name="admin.html", context={"show_admin_link": True}
    )


@router.get("/me")
def me_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = _current_user_from_session(request, db, settings)
    return templates.TemplateResponse(
        request=request,
        name="me.html",
        context={"show_admin_link": _show_admin_link_for_user(user)},
    )
