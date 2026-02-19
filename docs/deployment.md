# Deployment Guide

This app can run on any Linux VM/container that supports Python 3.12+.

## Production requirements

- HTTPS domain (required for WebAuthn/passkeys)
- Persistent database storage (recommended for real usage)
- Process supervisor (`systemd`, container platform, etc.)
- Reverse proxy (`caddy`, `nginx`, load balancer)

## Required environment variables

Set at least:

```env
APP_DATABASE_URL=sqlite:////absolute/path/to/app.db
APP_TOKEN_PEPPER=<long-random-secret>
APP_WEBAUTHN_RP_ID=<your-domain>
APP_WEBAUTHN_ORIGIN=https://<your-domain>
APP_SESSION_COOKIE_SECURE=true
APP_CORS_ALLOW_ORIGINS=https://<your-domain>
APP_INSECURE_DEV_WEBAUTHN=false
```

WebAuthn values must match your actual public origin/domain exactly.

## Minimal VM deployment pattern

1. Install system packages and `uv`.
2. Clone repository.
3. Run `uv sync --frozen`.
4. Create `.env` with production values.
5. Start app with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` under `systemd`.
6. Put `caddy` or `nginx` in front for TLS and public routing.

## GitHub Pages for docs

This repository includes a Pages workflow at `.github/workflows/pages.yml`.

1. Push to `main`.
2. In GitHub repository settings, open `Pages`.
3. Ensure source is **GitHub Actions**.
4. The workflow will publish content from `docs/`.
