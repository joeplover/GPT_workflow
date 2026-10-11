"""Local account authentication with opaque revocable HttpOnly sessions."""

from datetime import datetime, timedelta, timezone
import os
import re
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import database
from ..config import APP_PREFIX
from ..models import LoginAttempt, User, UserSession
from ..security import (
    COOKIE_NAME,
    digest,
    hash_password,
    require_actor,
    secure_cookie,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=100)


class ProfileInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


def user_info(user):
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
    }


def set_session(session: Session, user: User, response: Response):
    # Remove the previous root cookie to avoid duplicate session names on upgrade.
    response.delete_cookie(COOKIE_NAME, path="/")
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            token_hash=digest(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=7 * 86400,
        httponly=True,
        secure=secure_cookie(),
        samesite="lax",
        path=APP_PREFIX,
    )


def check_rate(session: Session, request: Request):
    key = digest(request.client.host if request.client else "unknown")
    # Serialize per-address admission across workers, including parallel attempts.
    lock = int(key[:15], 16)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    count = session.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.key == key, LoginAttempt.created_at >= cutoff)
    )
    if count >= 20:
        raise HTTPException(429, "尝试次数过多，请在 15 分钟后重试。")
    session.add(LoginAttempt(key=key))
    session.commit()


@router.get("/options")
def options():
    return {
        "registration_enabled": os.getenv("ALLOW_REGISTRATION", "true").lower()
        == "true"
    }


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, response: Response):
    if not options()["registration_enabled"]:
        raise HTTPException(403, "当前未开放注册，请联系管理员。")
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_\-.]{3,64}", username):
        raise HTTPException(
            400, "用户名须为 3–64 位英文字母、数字、下划线、点或短横线。"
        )
    if len(body.password) < 10:
        raise HTTPException(400, "密码至少需要 10 个字符。")
    with Session(database.get_engine()) as session:
        check_rate(session, request)
        user = User(
            id=uuid.uuid4().hex,
            username=username,
            display_name=body.display_name.strip() or username,
            password_hash=hash_password(body.password),
            is_admin=False,
        )
        session.add(user)
        try:
            session.flush()
            set_session(session, user, response)
            result = user_info(user)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "用户名已被使用。") from exc
        return result


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    with Session(database.get_engine()) as session:
        check_rate(session, request)
        user = session.scalar(
            select(User).where(User.username == body.username.strip().lower())
        )
        # Use a real KDF for unknown users too, avoiding a cheap username timing oracle.
        encoded = user.password_hash if user else "scrypt$missing$" + "0" * 128
        if (
            not verify_password(body.password, encoded)
            or user is None
            or not user.is_active
        ):
            raise HTTPException(401, "用户名或密码不正确。")
        session.execute(
            delete(UserSession).where(
                UserSession.expires_at < datetime.now(timezone.utc)
            )
        )
        set_session(session, user, response)
        result = user_info(user)
        session.commit()
        return result


@router.get("/me")
def me():
    return user_info(require_actor())


@router.post("/logout")
def logout(request: Request, response: Response):
    with Session(database.get_engine()) as session, session.begin():
        session.execute(
            delete(UserSession).where(
                UserSession.token_hash == digest(request.cookies.get(COOKIE_NAME, ""))
            )
        )
    response.delete_cookie(COOKIE_NAME, path=APP_PREFIX)
    return {"ok": True}


@router.post("/profile")
def profile(body: ProfileInput):
    with Session(database.get_engine()) as session, session.begin():
        user = session.get(User, require_actor().id)
        user.display_name = body.display_name.strip() or user.username
        return user_info(user)


@router.post("/password")
def password(body: PasswordInput, response: Response):
    with Session(database.get_engine()) as session, session.begin():
        user = session.get(User, require_actor().id, with_for_update=True)
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(400, "当前密码不正确。")
        user.password_hash = hash_password(body.new_password)
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    response.delete_cookie(COOKIE_NAME, path=APP_PREFIX)
    return {"ok": True}
