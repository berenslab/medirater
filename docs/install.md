# Install and Run Locally

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

## 1) Install dependencies

```bash
uv sync --dev
```

## 2) Configure environment

Copy the template:

```bash
cp .env.example .env
```

For local development, verify these values:

```env
APP_DATABASE_URL=sqlite:///./app.db
APP_WEBAUTHN_RP_ID=localhost
APP_WEBAUTHN_ORIGIN=http://localhost:2008
APP_SESSION_COOKIE_SECURE=false
APP_INSECURE_DEV_WEBAUTHN=false
```

If you run on another local port, update `APP_WEBAUTHN_ORIGIN` accordingly.

## 3) Run the app

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 2008
```

Useful URLs:

- `http://127.0.0.1:2008/login`
- `http://127.0.0.1:2008/admin/signup`
- `http://127.0.0.1:2008/questionnaires`
- `http://127.0.0.1:2008/users`
- `http://127.0.0.1:2008/assigned`
- `http://127.0.0.1:2008/account`
- `http://127.0.0.1:2008/docs` (OpenAPI UI)

## 4) Bootstrap first superadmin

Create a one-time signup token:

```bash
uv run python scripts/create_bootstrap_token.py --expires-in-minutes 120
```

Use that token at `http://127.0.0.1:2008/admin/signup`.

## Troubleshooting

If `uv run uvicorn ...` fails after renaming the repo directory, rebuild entrypoints:

```bash
uv sync --reinstall
```
