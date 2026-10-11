"""Database sessions and request-scoped authorization shared by every project API."""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from . import database
from .models import Project, TeamMember, User, UserSession

COOKIE_NAME = "workflow_session"


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    display_name: str
    is_admin: bool = False
    is_active: bool = True


# None is reserved for local maintenance, never accepted by HTTP middleware.
current_actor: ContextVar[Actor | None] = ContextVar("current_actor", default=None)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${key}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=16384, r=8, p=1
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def actor_from_user(user: User) -> Actor:
    return Actor(
        user.id, user.username, user.display_name, user.is_admin, user.is_active
    )


def session_actor(token: str) -> Actor | None:
    if not token or len(token) > 256:
        return None
    with Session(database.get_engine()) as session:
        user = session.scalar(
            select(User)
            .join(UserSession)
            .where(
                UserSession.token_hash == digest(token),
                UserSession.expires_at > datetime.now(timezone.utc),
                User.is_active.is_(True),
            )
        )
        return actor_from_user(user) if user else None


def require_actor() -> Actor:
    actor = current_actor.get()
    if not actor:
        raise HTTPException(401, "请先登录。")
    return actor


def project_scope(actor: Actor):
    teams = select(TeamMember.team_id).where(TeamMember.user_id == actor.id)
    return or_(
        and_(Project.owner_id == actor.id, Project.team_id.is_(None)),
        Project.team_id.in_(teams),
    )


def require_project_access(session: Session, row: Project) -> None:
    actor = current_actor.get()
    if actor is None:
        return
    if row.team_id is None and row.owner_id == actor.id:
        return
    if row.team_id and session.get(TeamMember, (row.team_id, actor.id)):
        return
    raise HTTPException(404, "项目不存在或无权访问。")


def require_team(session: Session, team_id: str, manage: bool = False) -> TeamMember:
    member = session.get(TeamMember, (team_id, require_actor().id))
    if not member:
        raise HTTPException(404, "团队不存在或无权访问。")
    if manage and member.role not in {"owner", "admin"}:
        raise HTTPException(403, "只有团队所有者或管理员可以执行此操作。")
    return member


def secure_cookie() -> bool:
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"
