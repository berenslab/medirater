DEFAULT_CONSENT_TEXT = (
    "I confirm I understand this evaluation, I agree to submit my responses for this "
    "questionnaire version, and I understand my responses are stored for analysis."
)


def normalize_optional_consent_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def resolve_effective_consent_text(value: str | None) -> str:
    normalized = normalize_optional_consent_text(value)
    if normalized:
        return normalized
    return DEFAULT_CONSENT_TEXT
