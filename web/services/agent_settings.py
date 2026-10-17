"""User-scoped Xiao Meng Agent endpoint settings."""

import os
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..config import get_settings
from ..credential_crypto import CredentialEncryptionError, decrypt_api_key, encrypt_api_key
from ..errors import protect_secret
from ..models import User, UserAgentConfig
from ..security import require_actor


def default_agent_settings() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "configured": False,
        "base_url": settings["base_url"],
        "has_api_key": bool(settings["api_key"]),
        "model": os.getenv("PHOTODREAM_AGENT_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini",
    }


def owned_agent_config(session: Session) -> UserAgentConfig | None:
    return session.scalar(
        select(UserAgentConfig).where(UserAgentConfig.user_id == require_actor().id)
    )


def public_agent_config(config: UserAgentConfig | None) -> dict[str, str | bool]:
    if config is None:
        return default_agent_settings()
    return {
        "configured": True,
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key),
        "model": config.model,
    }


def validate_agent_values(base_url: str, model: str, api_key: str) -> tuple[str, str, str]:
    url, model, key = base_url.strip().rstrip("/"), model.strip(), api_key.strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0
    except ValueError:
        valid = False
    if (
        not valid
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or any(char.isspace() for char in url)
        or any(ord(char) < 32 or ord(char) == 127 for char in url)
    ):
        raise HTTPException(400, "请填写有效的 HTTP/HTTPS Agent 地址，不含账号、密码、查询参数或片段。")
    if not model or any(ord(char) < 32 or ord(char) == 127 for char in model):
        raise HTTPException(400, "请填写有效的 Agent 模型名称，不含换行或控制字符。")
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise HTTPException(400, "Agent Key 不能包含换行或控制字符。")
    return url, model, key


def encrypted_agent_key(config: UserAgentConfig, api_key: str) -> str:
    try:
        return encrypt_api_key(config, api_key)
    except CredentialEncryptionError as exc:
        raise HTTPException(503, str(exc)) from None


def resolve_agent_settings() -> dict[str, str | int | bool]:
    """Resolve the current user's Agent config, falling back to server env vars."""
    fallback = {
        **get_settings(),
        "model": os.getenv("PHOTODREAM_AGENT_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        "configured": False,
    }
    with Session(database.get_engine()) as session:
        config = owned_agent_config(session)
        if config is None:
            protect_secret(fallback["api_key"])
            return fallback
        try:
            key = decrypt_api_key(config)
        except CredentialEncryptionError as exc:
            raise HTTPException(503, str(exc)) from None
        protect_secret(key)
        return {
            **fallback,
            "base_url": config.base_url,
            "api_key": key,
            "model": config.model,
            "configured": True,
        }


def save_agent_settings(base_url: str, model: str, api_key: str) -> dict[str, str | bool]:
    url, model, key = validate_agent_values(base_url, model, api_key)
    with Session(database.get_engine()) as session, session.begin():
        actor = require_actor()
        session.execute(select(User.id).where(User.id == actor.id).with_for_update())
        config = owned_agent_config(session)
        if config is None:
            if not key:
                raise HTTPException(400, "新增 Agent 配置时请填写 Agent Key。")
            config = UserAgentConfig(
                id=uuid.uuid4().hex,
                user_id=actor.id,
                base_url=url,
                api_key="",
                model=model,
            )
            session.add(config)
        elif url != config.base_url and not key:
            raise HTTPException(400, "修改 Agent 地址时请同时填写 Agent Key，确认这一组配置。")
        config.base_url, config.model = url, model
        if key:
            config.api_key = encrypted_agent_key(config, key)
        session.flush()
        return public_agent_config(config)
