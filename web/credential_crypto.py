"""Authenticated encryption for API credentials, with keys outside PostgreSQL."""

import json
import os
from pathlib import Path
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from . import config
from .models import UserApiProfile

CIPHERTEXT_PREFIX = "pdenc:v1:"


class CredentialEncryptionError(RuntimeError):
    """Safe to report: never includes a credential, token or encryption key."""


def key_file() -> Path:
    configured = os.getenv("PHOTODREAM_ENCRYPTION_KEY_FILE", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else config.RUNTIME_DIR / "secrets" / "api-keys.key"
    )


def load_cipher() -> Fernet:
    key = os.getenv("PHOTODREAM_ENCRYPTION_KEY", "").strip()
    if not key:
        try:
            key = key_file().read_bytes().strip()
        except OSError:
            raise CredentialEncryptionError(
                "无法读取接口加密密钥，请恢复原密钥文件或设置 PHOTODREAM_ENCRYPTION_KEY。"
            ) from None
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise CredentialEncryptionError(
            "接口加密密钥格式无效，需要 Fernet 生成的 32 字节 URL-safe Base64 密钥。"
        ) from None


def create_key_file() -> None:
    target = key_file()
    temporary = target.with_name(target.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Publish a complete file without ever replacing an existing key.
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        raise CredentialEncryptionError(
            "无法创建接口加密密钥文件，请检查目录权限或设置 PHOTODREAM_ENCRYPTION_KEY。"
        ) from None


def credential_context(profile) -> list[str]:
    # Bind ciphertext to its owner, profile and destination to detect row swaps.
    return [profile.user_id, profile.id, profile.base_url]


def encrypt_api_key(profile, api_key: str, cipher: Fernet | None = None) -> str:
    if not isinstance(api_key, str) or not api_key:
        raise CredentialEncryptionError("接口 Key 不能为空，请在设置中重新填写。")
    cipher = cipher or load_cipher()
    payload = json.dumps(
        {"context": credential_context(profile), "api_key": api_key}, ensure_ascii=False
    ).encode("utf-8")
    return CIPHERTEXT_PREFIX + cipher.encrypt(payload).decode("ascii")


def decrypt_api_key(profile, cipher: Fernet | None = None) -> str:
    cipher = cipher or load_cipher()
    try:
        if not profile.api_key.startswith(CIPHERTEXT_PREFIX):
            raise ValueError("unmigrated credential")
        token = profile.api_key[len(CIPHERTEXT_PREFIX) :].encode("ascii")
        payload = json.loads(cipher.decrypt(token))
        if payload["context"] != credential_context(profile):
            raise ValueError("credential context mismatch")
        key = payload["api_key"]
        if not isinstance(key, str) or not key:
            raise ValueError("invalid credential")
        return key
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
        raise CredentialEncryptionError(
            "接口 Key 解密失败，请检查是否使用原加密密钥、配置是否被修改，或在设置中重新填写 Key。"
        ) from None


def initialize_api_key_encryption(session) -> None:
    """Called under the startup DB advisory lock, in the schema transaction."""
    if (
        not os.getenv("PHOTODREAM_ENCRYPTION_KEY", "").strip()
        and not key_file().exists()
    ):
        encrypted = session.scalar(
            select(UserApiProfile.id)
            .where(UserApiProfile.api_key.startswith(CIPHERTEXT_PREFIX))
            .limit(1)
        )
        if encrypted:
            raise CredentialEncryptionError(
                "数据库已有加密 Key，但缺少原加密密钥；请恢复密钥，系统不会生成新密钥覆盖。"
            )
        create_key_file()
    cipher = load_cipher()
    for profile in session.scalars(select(UserApiProfile).with_for_update()).yield_per(
        100
    ):
        if profile.api_key.startswith(CIPHERTEXT_PREFIX):
            decrypt_api_key(profile, cipher)
        else:
            profile.api_key = encrypt_api_key(profile, profile.api_key, cipher)
    session.flush()
