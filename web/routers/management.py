"""Team collaboration and project lifecycle. All checks run against the database."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets
import uuid
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import cast, delete, literal, or_, select, func
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import database
from ..models import (
    Project,
    ProjectEvent,
    Team,
    TeamInvite,
    TeamMember,
    User,
    UserSession,
)
from ..repository import (
    document_changes,
    get_project,
    load_projects,
    mutate_project,
    now_iso,
    project_document,
)
from ..security import (
    digest,
    hash_password,
    require_actor,
    require_project_access,
    require_team,
)

router = APIRouter(prefix="/api", tags=["management"])


class NameInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = Field(default=None, max_length=20000)
    archived: bool | None = None


class ShareInput(BaseModel):
    team_id: str | None = None


class JoinInput(BaseModel):
    token: str = Field(min_length=20, max_length=100)


class RoleInput(BaseModel):
    role: str


class ImportInput(BaseModel):
    source_project_id: str
    card_type: str
    card_ids: list[str] = Field(min_length=1, max_length=100)


class AdminUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(default="", max_length=100)
    password: str = Field(min_length=10, max_length=128)


class UserStatusInput(BaseModel):
    is_active: bool


class ResetPasswordInput(BaseModel):
    password: str = Field(min_length=10, max_length=128)


def manageable_project(session, row):
    actor = require_actor()
    require_project_access(session, row)
    if row.team_id:
        require_team(session, row.team_id, manage=True)
    elif row.owner_id != actor.id:
        raise HTTPException(403, "只有项目所有者可以执行此操作。")


@router.post("/projects/{project_id}/metadata")
def metadata(project_id: str, body: ProjectInput):
    # mutate_project checks access again under the project row lock.
    def change(project):
        if body.archived is not None:
            with Session(database.get_engine()) as session:
                manageable_project(session, session.get(Project, project_id))
        updates = body.model_dump(exclude_none=True)
        if "name" in updates:
            updates["name"] = updates["name"].strip()
            if not updates["name"]:
                raise HTTPException(400, "项目名称不能为空。")
        project.update(updates)

    return mutate_project(project_id, change, "project.metadata")


@router.post("/projects/{project_id}/sharing")
def sharing(project_id: str, body: ShareInput):
    actor = require_actor()
    with Session(database.get_engine()) as session, session.begin():
        row = session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if not row:
            raise HTTPException(404, "项目不存在。")
        manageable_project(session, row)
        if body.team_id:
            require_team(session, body.team_id)
        # Only the team owner may take a shared project out of its team.
        if row.team_id and row.team_id != body.team_id:
            member = require_team(session, row.team_id, manage=True)
            if member.role != "owner":
                raise HTTPException(403, "只有团队所有者可以迁移团队项目。")
        before = {"owner_id": row.owner_id, "team_id": row.team_id}
        row.team_id = body.team_id or None
        row.owner_id = actor.id
        row.updated_at = func.now()
        after = {"owner_id": row.owner_id, "team_id": row.team_id}
        if before != after:
            session.add(
                ProjectEvent(
                    project_id=project_id,
                    actor_id=actor.id,
                    operation="project.sharing",
                    changes=document_changes(before, after),
                )
            )
        return project_document(row)


@router.get("/teams")
def teams():
    with Session(database.get_engine()) as session:
        rows = session.execute(
            select(Team, TeamMember.role)
            .join(TeamMember)
            .where(TeamMember.user_id == require_actor().id)
            .order_by(Team.created_at.desc())
        )
        return [
            {
                "id": team.id,
                "name": team.name,
                "role": role,
                "created_at": team.created_at.isoformat(),
            }
            for team, role in rows
        ]


@router.post("/teams", status_code=201)
def create_team(body: NameInput):
    with Session(database.get_engine()) as session, session.begin():
        team = Team(id=uuid.uuid4().hex, name=body.name.strip())
        if not team.name:
            raise HTTPException(400, "团队名称不能为空。")
        session.add(team)
        session.flush()
        session.add(
            TeamMember(team_id=team.id, user_id=require_actor().id, role="owner")
        )
        return {"id": team.id, "name": team.name, "role": "owner"}


@router.post("/teams/join")
def join_team(body: JoinInput):
    with Session(database.get_engine()) as session, session.begin():
        invite = session.get(TeamInvite, digest(body.token), with_for_update=True)
        if not invite or invite.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(400, "邀请已过期、已使用或不存在。")
        # Revoked inviter privileges invalidate outstanding invitations.
        inviter = session.get(TeamMember, (invite.team_id, invite.created_by))
        if not inviter or inviter.role not in {"owner", "admin"}:
            raise HTTPException(400, "邀请已失效。")
        actor = require_actor()
        if session.get(TeamMember, (invite.team_id, actor.id)):
            raise HTTPException(409, "你已在这个团队中。")
        session.add(TeamMember(team_id=invite.team_id, user_id=actor.id, role="member"))
        team = session.get(Team, invite.team_id)
        session.delete(invite)
        return {"id": team.id, "name": team.name, "role": "member"}


@router.get("/teams/{team_id}/members")
def members(team_id: str):
    with Session(database.get_engine()) as session:
        require_team(session, team_id)
        rows = session.execute(
            select(User, TeamMember.role)
            .join(TeamMember)
            .where(TeamMember.team_id == team_id)
            .order_by(User.created_at)
        )
        return [
            {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": role,
            }
            for user, role in rows
        ]


@router.post("/teams/{team_id}/name")
def rename_team(team_id: str, body: NameInput):
    with Session(database.get_engine()) as session, session.begin():
        require_team(session, team_id, manage=True)
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "团队名称不能为空。")
        session.get(Team, team_id).name = name
    return {"ok": True}


@router.post("/teams/{team_id}/invites")
def invite_member(team_id: str):
    with Session(database.get_engine()) as session, session.begin():
        require_team(session, team_id, manage=True)
        token = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(days=2)
        session.add(
            TeamInvite(
                token_hash=digest(token),
                team_id=team_id,
                expires_at=expires,
                created_by=require_actor().id,
            )
        )
        return {"token": token, "expires_at": expires.isoformat()}


@router.delete("/teams/{team_id}/invites")
def revoke_invites(team_id: str):
    with Session(database.get_engine()) as session, session.begin():
        require_team(session, team_id, manage=True)
        session.execute(delete(TeamInvite).where(TeamInvite.team_id == team_id))
    return {"ok": True}


@router.post("/teams/{team_id}/members/{user_id}/role")
def member_role(team_id: str, user_id: str, body: RoleInput):
    if body.role not in {"admin", "member"}:
        raise HTTPException(400, "角色必须为 admin 或 member。")
    with Session(database.get_engine()) as session, session.begin():
        # Lock team before membership edits; the owner cannot be demoted here.
        session.get(Team, team_id, with_for_update=True)
        manager = require_team(session, team_id, manage=True)
        if manager.role != "owner":
            raise HTTPException(403, "只有团队所有者可以调整角色。")
        member = session.get(TeamMember, (team_id, user_id))
        if not member:
            raise HTTPException(404, "成员不存在。")
        if member.role == "owner":
            raise HTTPException(400, "不能修改团队所有者的角色。")
        member.role = body.role
    return {"ok": True}


@router.delete("/teams/{team_id}/members/{user_id}")
def remove_member(team_id: str, user_id: str):
    with Session(database.get_engine()) as session, session.begin():
        session.get(Team, team_id, with_for_update=True)
        manager = require_team(session, team_id)
        member = session.get(TeamMember, (team_id, user_id))
        if not member:
            raise HTTPException(404, "成员不存在。")
        if member.role == "owner":
            raise HTTPException(400, "团队所有者不能退出或被移除。")
        if user_id != require_actor().id and (
            manager.role == "member"
            or (manager.role == "admin" and member.role == "admin")
        ):
            raise HTTPException(403, "无权移除此成员。")
        session.delete(member)
    return {"ok": True}


@router.get("/resources")
def resources(team_id: str | None = None):
    result = []
    for project in load_projects():
        if project.get("archived") or (team_id and project.get("team_id") != team_id):
            continue
        for kind, key in (("character", "character_cards"), ("scene", "scene_cards")):
            for card in project[key]:
                result.append(
                    {
                        **card,
                        "card_type": kind,
                        "project_id": project["id"],
                        "project_name": project["name"],
                        "team_id": project["team_id"],
                    }
                )
    return result


@router.post("/projects/{project_id}/resources/import")
def import_resources(project_id: str, body: ImportInput):
    get_project(project_id)
    source = get_project(body.source_project_id)
    if body.card_type not in {"character", "scene"}:
        raise HTTPException(400, "无效资源类型。")
    key = body.card_type + "_cards"
    selected = set(body.card_ids)
    cards = [deepcopy(c) for c in source[key] if c["id"] in selected]
    if len(cards) != len(selected):
        raise HTTPException(404, "部分资源不存在。")
    for card in cards:
        card.update(id=uuid.uuid4().hex, created_at=now_iso())
    return mutate_project(project_id, lambda p: p[key].extend(cards), "cards.import")


@router.get("/admin/users")
def admin_users():
    if not require_actor().is_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    with Session(database.get_engine()) as session:
        return [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "is_admin": u.is_admin,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat(),
            }
            for u in session.scalars(select(User).order_by(User.created_at))
        ]


@router.post("/admin/users", status_code=201)
def create_user(body: AdminUserInput):
    if not require_actor().is_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_\-.]{3,64}", username):
        raise HTTPException(400, "用户名格式不正确。")
    with Session(database.get_engine()) as session, session.begin():
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
        except IntegrityError as exc:
            raise HTTPException(409, "用户名已被使用。") from exc
        return {"id": user.id, "username": user.username}


@router.post("/admin/users/{user_id}/status")
def user_status(user_id: str, body: UserStatusInput):
    actor = require_actor()
    if not actor.is_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    if user_id == actor.id:
        raise HTTPException(400, "不能停用当前管理员账号。")
    with Session(database.get_engine()) as session, session.begin():
        user = session.get(User, user_id, with_for_update=True)
        if not user:
            raise HTTPException(404, "用户不存在。")
        user.is_active = body.is_active
        if not body.is_active:
            session.execute(delete(UserSession).where(UserSession.user_id == user_id))
    return {"ok": True}


@router.post("/admin/users/{user_id}/password")
def reset_user_password(user_id: str, body: ResetPasswordInput):
    if not require_actor().is_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    if user_id == require_actor().id:
        raise HTTPException(400, "请在账号设置中修改自己的密码。")
    with Session(database.get_engine()) as session, session.begin():
        user = session.get(User, user_id, with_for_update=True)
        if not user:
            raise HTTPException(404, "用户不存在。")
        user.password_hash = hash_password(body.password)
        session.execute(delete(UserSession).where(UserSession.user_id == user_id))
    return {"ok": True}


def authorize_asset(url: str):
    """Only server-owned image fields confer access, never user prompt strings."""
    from ..security import project_scope

    variables = literal({"url": url}, type_=JSONB)
    paths = [
        "$.**.image_url ? (@ == $url)",
        "$.**.source_image ? (@ == $url)",
        "$.**.mask_image ? (@ == $url)",
        "$.**.reference_images[*] ? (@ == $url)",
    ]
    predicate = or_(
        *(
            func.jsonb_path_exists(Project.document, cast(path, JSONPATH), variables)
            for path in paths
        )
    )
    with Session(database.get_engine()) as session:
        scope = project_scope(require_actor())
        if session.scalar(select(Project.id).where(scope, predicate).limit(1)):
            return
        history_paths = paths + [
            '$[*] ? (@.path[last] == "image_url" || @.path[last] == "source_image" || @.path[last] == "mask_image").before ? (@ == $url)',
            '$[*] ? (@.path[last] == "image_url" || @.path[last] == "source_image" || @.path[last] == "mask_image").after ? (@ == $url)',
            '$[*] ? (@.path[last] == "reference_images").before[*] ? (@ == $url)',
            '$[*] ? (@.path[last] == "reference_images").after[*] ? (@ == $url)',
        ]
        historic = or_(
            *(
                func.jsonb_path_exists(
                    ProjectEvent.changes, cast(path, JSONPATH), variables
                )
                for path in history_paths
            )
        )
        if session.scalar(
            select(Project.id)
            .join(ProjectEvent, ProjectEvent.project_id == Project.id)
            .where(scope, historic)
            .limit(1)
        ):
            return
    raise HTTPException(404, "文件不存在或无权访问。")
