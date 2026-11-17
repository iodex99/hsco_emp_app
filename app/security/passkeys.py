"""WebAuthn (security keys, Windows Hello, phone passkeys). Requires HTTPS (or http://localhost)."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement,
)

from app.config import get_config
from app.models import MfaMethod, User, UserSession


def _origins() -> list[str]:
    cfg = get_config()
    return [o.strip() for o in cfg.rp_origin.split(",") if o.strip()]


def registration_options(db: Session, sess: UserSession, user: User, org: str) -> str:
    cfg = get_config()
    existing = [m for m in user.mfa_methods if m.kind == "webauthn" and m.credential_id]
    opts = generate_registration_options(
        rp_id=cfg.rp_id, rp_name=org, user_name=user.username, user_id=f"user-{user.id}".encode(),
        user_display_name=user.full_name or user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=m.credential_id) for m in existing],
    )
    sess.pending = {**(sess.pending or {}), "webauthn_reg": bytes_to_base64url(opts.challenge)}
    return options_to_json(opts)


def finish_registration(db: Session, sess: UserSession, user: User, credential: dict, label: str) -> MfaMethod:
    cfg = get_config()
    chal = (sess.pending or {}).get("webauthn_reg")
    if not chal:
        raise ValueError("No registration in progress")
    v = verify_registration_response(credential=credential, expected_challenge=base64url_to_bytes(chal),
                                     expected_rp_id=cfg.rp_id, expected_origin=_origins())
    m = MfaMethod(user_id=user.id, kind="webauthn", label=label or "Security key", credential_id=v.credential_id,
                  public_key=v.credential_public_key, sign_count=v.sign_count, confirmed=True)
    db.add(m)
    p = dict(sess.pending)
    p.pop("webauthn_reg", None)
    sess.pending = p
    return m


def authentication_options(db: Session, sess: UserSession, user: User) -> str:
    cfg = get_config()
    creds = [m for m in user.mfa_methods if m.kind == "webauthn" and m.credential_id]
    opts = generate_authentication_options(rp_id=cfg.rp_id,
                                           allow_credentials=[PublicKeyCredentialDescriptor(id=m.credential_id) for m in creds],
                                           user_verification=UserVerificationRequirement.PREFERRED)
    sess.pending = {**(sess.pending or {}), "webauthn_auth": bytes_to_base64url(opts.challenge)}
    return options_to_json(opts)


def finish_authentication(db: Session, sess: UserSession, user: User, credential: dict) -> bool:
    cfg = get_config()
    chal = (sess.pending or {}).get("webauthn_auth")
    if not chal:
        return False
    raw_id = base64url_to_bytes(credential.get("rawId") or credential.get("id", ""))
    m = db.scalar(select(MfaMethod).where(MfaMethod.user_id == user.id, MfaMethod.kind == "webauthn",
                                          MfaMethod.credential_id == raw_id))
    if m is None:
        return False
    try:
        v = verify_authentication_response(credential=credential, expected_challenge=base64url_to_bytes(chal),
                                           expected_rp_id=cfg.rp_id, expected_origin=_origins(),
                                           credential_public_key=m.public_key, credential_current_sign_count=m.sign_count)
    except Exception:  # noqa: BLE001
        return False
    m.sign_count = v.new_sign_count
    m.last_used = datetime.now()
    p = dict(sess.pending)
    p.pop("webauthn_auth", None)
    sess.pending = p
    return True


def dumps(o) -> str:
    return json.dumps(o)
