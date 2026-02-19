# API Overview

## OpenAPI

- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`

## Health

- `GET /health`

## Auth (`/api/auth`)

- `GET /public-signup-mode`
- `POST /signup/begin`
- `POST /signup/complete`
- `POST /login/begin`
- `POST /login/complete`
- `POST /logout`
- `GET /me`
- `PATCH /me`

## Passkeys (`/api/passkeys`)

- `GET /api/passkeys`
- `POST /api/passkeys/begin-add`
- `POST /api/passkeys/complete-add`
- `PATCH /api/passkeys/{passkey_id}`
- `DELETE /api/passkeys/{passkey_id}`

## Admin settings/users/assignments (`/api/admin`)

- Signup mode:
  - `GET /api/admin/settings/public-signup-mode`
  - `PUT /api/admin/settings/public-signup-mode`
- Signup tokens:
  - `POST /api/admin/signup-tokens`
  - `GET /api/admin/signup-tokens`
  - `GET /api/admin/signup-token-scope-options`
- User management:
  - `GET /api/admin/users`
  - `PATCH /api/admin/users/{username}`
- Assignments:
  - `GET /api/admin/assignments`
  - `POST /api/admin/assignments`
  - `PUT /api/admin/assignments/bulk`
  - `PATCH /api/admin/assignments/{assignment_id}`

## Admin questionnaires (`/api/admin/questionnaires`)

Major endpoints:

- CRUD:
  - `GET /api/admin/questionnaires`
  - `POST /api/admin/questionnaires`
  - `GET /api/admin/questionnaires/{questionnaire_id}`
  - `PATCH /api/admin/questionnaires/{questionnaire_id}`
  - `DELETE /api/admin/questionnaires/{questionnaire_id}`
- Versions:
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions`
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/publish`
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/unpublish`
- Question editing:
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions`
  - `PATCH /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions/{question_id}`
  - `DELETE /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/questions/{question_id}`
- Responses:
  - `GET /api/admin/questionnaires/{questionnaire_id}/responses`
  - `GET /api/admin/questionnaires/{questionnaire_id}/responses/export.csv`
- Bulk recipes:
  - `GET /api/admin/questionnaires/bulk-recipes/catalog`
  - `GET /api/admin/questionnaires/bulk-recipes/{recipe_type}/design`
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview`
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply`
  - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply-preview`

## Admin assets (`/api/admin/assets`)

- `POST /api/admin/assets/upload`
- `GET /api/admin/assets`
- `DELETE /api/admin/assets/questionnaire/{questionnaire_id}`
- `GET /api/admin/assets/{asset_id}`
- `GET /api/admin/assets/{asset_id}/content`

## User answering (`/api/user`)

- `GET /api/user/assigned-questionnaires`
- `GET /api/user/questionnaires/{questionnaire_version_id}`
- `GET /api/user/questionnaires/{questionnaire_version_id}/consent`
- `POST /api/user/questionnaires/{questionnaire_version_id}/consent`
- `POST /api/user/questionnaires/{questionnaire_version_id}/submit`
- `GET /api/user/questionnaires/{questionnaire_version_id}/assets/{asset_id}/content`
