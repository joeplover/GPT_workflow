"""Credential lookup is always scoped to the current user, including admins."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..credential_crypto import (
    CredentialEncryptionError,
    decrypt_api_key,
    encrypt_api_key,
)
from ..models import UserApiProfile
from ..security import require_actor


def owned_profile(session: Session, profile_id: str) -> UserApiProfile:
    profile = session.scalar(
        select(UserApiProfile).where(
            UserApiProfile.id == profile_id,
            UserApiProfile.user_id == require_actor().id,
        )
    )
    if profile is None:
        raise HTTPException(404, "接口配置不存在或已删除，请重新选择。")
    return profile


def profile_credentials(profile_id: str) -> tuple[str, str]:
    with Session(database.get_engine()) as session:
        profile = owned_profile(session, profile_id)
        try:
            return decrypt_api_key(profile), profile.base_url
        except CredentialEncryptionError as exc:
            raise HTTPException(503, str(exc)) from None


def selected_profile_id() -> str:
    """Return the current user's selected image profile, if one exists."""
    with Session(database.get_engine()) as session:
        profile = session.scalar(
            select(UserApiProfile.id).where(
                UserApiProfile.user_id == require_actor().id,
                UserApiProfile.selected.is_(True),
            )
        )
        return profile or ""


def encrypted_profile_key(profile, api_key: str) -> str:
    try:
        return encrypt_api_key(profile, api_key)
    except CredentialEncryptionError as exc:
        raise HTTPException(503, str(exc)) from None
