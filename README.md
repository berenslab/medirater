# Medirater

`medirater` is a questionaire web app for evalutating medical images.

## Requirements

- Python 3.12+
- `uv`

## 1) Install dependencies

```bash
uv sync --dev
```

## 2) Configure environment

Copy the template:

```bash
cp .env.example .env
```

For local run, keep at least:

```env
APP_WEBAUTHN_RP_ID=localhost
APP_WEBAUTHN_ORIGIN=http://localhost:8000
```

## 3) Start the app

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The app is available at:

- `http://localhost:8000/login`
- `http://localhost:8000/admin_signup`
- `http://localhost:8000/questionnaires`
- `http://localhost:8000/users`
- `http://localhost:8000/assigned`
- `http://localhost:8000/account`
- `http://localhost:8000/docs`

## 4) Create first superadmin (bootstrap)

Generate a one-time signup token:

```bash
uv run python scripts/create_bootstrap_token.py --expires-in-minutes 120
```

Use that token at:

- `http://localhost:8000/admin_signup`

After creating the first superadmin account, manage signup mode and invite tokens from:

- `http://localhost:8000/users`
