import json
from dataclasses import dataclass
from typing import Any

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
)
from webauthn import verify_authentication_response as webauthn_verify_authentication_response
from webauthn import verify_registration_response as webauthn_verify_registration_response
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


@dataclass
class RegistrationResult:
    credential_id: str
    public_key: str
    sign_count: int


@dataclass
class AuthenticationResult:
    credential_id: str
    new_sign_count: int


def extract_credential_id(credential: dict[str, Any]) -> str:
    credential_id = credential.get("id") or credential.get("rawId")
    if not credential_id or not isinstance(credential_id, str):
        raise ValueError("Credential payload missing id")
    return credential_id


def generate_registration_public_key_options(
    *,
    rp_id: str,
    rp_name: str,
    username: str,
    user_id: str,
    exclude_credential_ids: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    exclude: list[PublicKeyCredentialDescriptor] = []
    for credential_id in exclude_credential_ids or []:
        try:
            exclude.append(PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id)))
        except Exception:
            continue

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=username,
        user_id=user_id.encode("utf-8"),
        user_display_name=username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude,
    )
    options_payload = json.loads(options_to_json(options))
    challenge = options_payload.get("challenge")
    if not isinstance(challenge, str) or not challenge:
        raise ValueError("Failed to generate registration challenge")
    return options_payload, challenge


def generate_authentication_public_key_options(
    *,
    rp_id: str,
    allow_credential_ids: list[str],
) -> tuple[dict[str, Any], str]:
    allow: list[PublicKeyCredentialDescriptor] = []
    for credential_id in allow_credential_ids:
        try:
            allow.append(PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id)))
        except Exception:
            continue
    if not allow:
        raise ValueError("No valid passkeys registered for this user")

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    options_payload = json.loads(options_to_json(options))
    challenge = options_payload.get("challenge")
    if not isinstance(challenge, str) or not challenge:
        raise ValueError("Failed to generate authentication challenge")
    return options_payload, challenge


def verify_registration_response(
    *,
    challenge: str,
    credential: dict[str, Any],
    expected_rp_id: str,
    expected_origin: str,
    insecure_dev_webauthn: bool,
) -> RegistrationResult:
    if insecure_dev_webauthn:
        credential_id = extract_credential_id(credential)
        response = credential.get("response") if isinstance(credential, dict) else None
        public_key = ""
        if isinstance(response, dict):
            attestation_object = response.get("attestationObject")
            if isinstance(attestation_object, str):
                public_key = attestation_object
        if not public_key:
            public_key = bytes_to_base64url(f"devpk:{credential_id}".encode("utf-8"))
        return RegistrationResult(
            credential_id=credential_id,
            public_key=public_key,
            sign_count=0,
        )

    try:
        verification = webauthn_verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=expected_rp_id,
            expected_origin=expected_origin,
            require_user_verification=False,
        )
    except InvalidRegistrationResponse as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        raise ValueError("Invalid registration response") from exc

    return RegistrationResult(
        credential_id=bytes_to_base64url(verification.credential_id),
        public_key=bytes_to_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
    )


def verify_authentication_response(
    *,
    challenge: str,
    credential: dict[str, Any],
    credential_public_key: str,
    current_sign_count: int,
    expected_rp_id: str,
    expected_origin: str,
    insecure_dev_webauthn: bool,
) -> AuthenticationResult:
    if insecure_dev_webauthn:
        credential_id = extract_credential_id(credential)
        return AuthenticationResult(credential_id=credential_id, new_sign_count=current_sign_count + 1)

    try:
        verification = webauthn_verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=expected_rp_id,
            expected_origin=expected_origin,
            credential_public_key=base64url_to_bytes(credential_public_key),
            credential_current_sign_count=current_sign_count,
            require_user_verification=False,
        )
    except InvalidAuthenticationResponse as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        raise ValueError("Invalid authentication response") from exc

    return AuthenticationResult(
        credential_id=bytes_to_base64url(verification.credential_id),
        new_sign_count=verification.new_sign_count,
    )
