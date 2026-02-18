import re

from sqlalchemy.engine import Engine

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    normalized = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    if len(normalized) > 220:
        normalized = normalized[:220].rstrip("-")
    return normalized


def _dedupe_slug(base_slug: str, used: set[str]) -> str:
    candidate = base_slug
    suffix = 2
    while candidate in used:
        suffix_text = f"-{suffix}"
        allowed_base_len = max(1, 220 - len(suffix_text))
        candidate = f"{base_slug[:allowed_base_len].rstrip('-')}{suffix_text}"
        suffix += 1
    return candidate


def ensure_runtime_schema(engine: Engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.begin() as conn:
        table_info = conn.exec_driver_sql("PRAGMA table_info(questionnaires)").fetchall()
        if not table_info:
            return

        column_names = {row[1] for row in table_info}
        if "slug" not in column_names:
            conn.exec_driver_sql("ALTER TABLE questionnaires ADD COLUMN slug VARCHAR(220)")

        rows = conn.exec_driver_sql("SELECT id, title, slug FROM questionnaires").fetchall()
        used_slugs: set[str] = set()
        updates: list[tuple[str, str]] = []
        for questionnaire_id, title, slug in rows:
            raw_slug = str(slug or "").strip()
            if raw_slug:
                base = _slugify(raw_slug)
            else:
                base = _slugify(str(title or ""))

            if not base:
                base = f"questionnaire-{str(questionnaire_id)[:8]}"

            candidate = _dedupe_slug(base, used_slugs)
            used_slugs.add(candidate)
            if raw_slug != candidate:
                updates.append((candidate, questionnaire_id))

        for candidate, questionnaire_id in updates:
            conn.exec_driver_sql(
                "UPDATE questionnaires SET slug = ? WHERE id = ?",
                (candidate, questionnaire_id),
            )

        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_questionnaires_slug_unique ON questionnaires (slug)"
        )

        assets_table_info = conn.exec_driver_sql("PRAGMA table_info(assets)").fetchall()
        if assets_table_info:
            asset_column_names = {row[1] for row in assets_table_info}
            if "questionnaire_id" not in asset_column_names:
                conn.exec_driver_sql("ALTER TABLE assets ADD COLUMN questionnaire_id VARCHAR(36)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_assets_questionnaire_id ON assets (questionnaire_id)"
            )
