# Medirater (WIP)

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

## Environment

Use the template first:

```bash
cp .env.example .env
```

For local dev, keep:
- `APP_WEBAUTHN_RP_ID=localhost`
- `APP_WEBAUTHN_ORIGIN=http://localhost:8000`

For Fly.io (or any real domain), set:
- `APP_WEBAUTHN_RP_ID=<your-domain>` (domain only)
- `APP_WEBAUTHN_ORIGIN=https://<your-domain>`
- `APP_SESSION_COOKIE_SECURE=true`
- `APP_DATABASE_URL=sqlite:////data/app.db` (if using mounted volume at `/data`)

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
- Users (admin/superadmin): `http://localhost:8000/users`
- Assigned (user): `http://localhost:8000/assigned`
- Account: `http://localhost:8000/account`
- API docs: `http://localhost:8000/docs`

## Manual Test Path (Current)

1. Use bootstrap token on `/admin_signup` to create first `superadmin`.
2. In `/users`, create:
- one `admin` signup token
- one `user` signup token with questionnaire version scope (required)
3. Create an `admin` account using `/admin_signup`.
4. As admin or superadmin, go to `/questionnaires`:
- create/select questionnaire
- set questionnaire `slug` (must be unique)
- open dedicated questionnaire design page
- use bulk upload/import as the default question creation flow
- manually edit/delete existing generated questions when needed
- publish draft version
- open `/questionnaires/{questionnaire_id}/assignments` to assign users per questionnaire/version
- open `/questionnaires/{questionnaire_id}/responses` to review submitted answers and export all responses to CSV
5. Go to `/users` for:
- signup mode control (superadmin only)
- signup token creation/list
6. Go to `/account` for:
- username / years-of-experience / passkey management
7. Login as a scoped `user` and go to `/assigned` to open `/answer/{questionnaire_version_id}/consent`, then answer one page at a time via `/answer/{questionnaire_version_id}?q=N`.

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

## Fly.io Quick Test (Single Machine, FRA, No Volume)

This repo includes `Dockerfile` and `fly.toml` for a minimal Fly deployment:
- single shared-cpu machine
- `fra` primary region
- SQLite on `/tmp/app.db` (ephemeral)
- no Postgres, no persistent volume

### 1) Pick an app name

Edit `fly.toml` and change:

```toml
app = "medirater-fra-test"
```

to your unique Fly app name.

### 2) Create app + set required secrets

```bash
fly auth login
fly apps create <your-app-name>

fly secrets set \
  APP_TOKEN_PEPPER="$(openssl rand -hex 32)" \
  APP_WEBAUTHN_RP_ID="<your-app-name>.fly.dev" \
  APP_WEBAUTHN_ORIGIN="https://<your-app-name>.fly.dev" \
  APP_CORS_ALLOW_ORIGINS="https://<your-app-name>.fly.dev"
```

### 3) Deploy

```bash
fly deploy
fly scale count 1
fly open
```

### 4) Bootstrap superadmin

```bash
fly ssh console -C "uv run python scripts/create_bootstrap_token.py --expires-in-minutes 120"
```

Use that token at `/admin_signup`.

### Notes

- This setup is for short-lived testing only.
- Any restart/redeploy may wipe data because SQLite is on ephemeral `/tmp`.
