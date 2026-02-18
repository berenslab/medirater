# Medirator (WIP)

FastAPI app for role-based medical questionnaire authoring and evaluation.

Current scope includes:
- passkey auth with `user` / `admin` / `superadmin`
- questionnaire draft/publish workflow
- asset upload to SQLite BLOB storage
- recipe-based bulk question generation (preview/apply)
- split frontend pages for questionnaires, users, and settings

## Requirements

- Python 3.12+
- `uv`

## Install

```bash
uv sync --dev
```

## Optional `.env` (local dev)

Create `.env` in project root if needed:

```env
APP_TOKEN_PEPPER=change-me
APP_WEBAUTHN_RP_ID=localhost
APP_WEBAUTHN_ORIGIN=http://localhost:8000
APP_INSECURE_DEV_WEBAUTHN=true
```

Notes:
- Real passkeys work on `http://localhost`.
- `APP_INSECURE_DEV_WEBAUTHN=true` is dev-only.

## Run

1. Create bootstrap superadmin token:

```bash
uv run python scripts/create_bootstrap_token.py --expires-in-minutes 120
```

2. Start API:

```bash
uv run uvicorn app.main:app --reload
```

3. Open:
- Login: `http://localhost:8000/login`
- Admin signup (token required): `http://localhost:8000/admin_signup`
- Questionnaires: `http://localhost:8000/questionnaires`
- Users (superadmin): `http://localhost:8000/users`
- Assigned (user): `http://localhost:8000/assigned`
- Settings: `http://localhost:8000/settings`
- API docs: `http://localhost:8000/docs`

## Manual Test Path (Current)

1. Use bootstrap token on `/admin_signup` to create first `superadmin`.
2. In `/settings`, create:
- one `admin` signup token
- one `user` signup token with questionnaire version scope (required)
3. Create an `admin` account using `/admin_signup`.
4. As admin or superadmin, go to `/questionnaires`:
- create/select questionnaire
- open dedicated questionnaire design page
- use bulk upload/import as the default question creation flow
- manually edit/delete existing generated questions when needed
- publish draft version
 - open `/questionnaires/{questionnaire_id}/responses` to review submitted answers
5. Go to `/settings` for:
- passkey management
- signup mode control (superadmin only)
- signup token creation/list
6. Login as a scoped `user` and go to `/assigned` to open `/answer/{questionnaire_version_id}/consent`, then answer one page at a time via `/answer/{questionnaire_version_id}?q=N`.

## Bulk Recipe Types (Current)

- `single_per_file`
- `triplet_by_suffix`
- `paired_by_filename`
- `case_with_patches`

Apply endpoints:
- `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply`
- `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply-preview`

Reference: `docs/bulk_recipe_spec.md`

## Run Tests

```bash
uv run pytest
uv run ruff check
```
