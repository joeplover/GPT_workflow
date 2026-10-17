"""Conversation and orchestration API for the Xiao Meng agent."""

from copy import deepcopy
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..config import get_settings
from ..models import AgentSession
from ..repository import ensure_project, get_project
from ..security import Actor, current_actor, require_actor
from ..services.agent import (
    STAGES,
    STAGE_LABELS,
    apply_stage,
    empty_plan,
    respond,
)

router = APIRouter(prefix="/api/agent", tags=["xiao meng agent"])


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(default="", max_length=100)
    mode: str = Field(default="guided", pattern="^(guided|autonomous)$")
    api_profile_id: str = Field(default="", max_length=100)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20000)


class ModeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(guided|autonomous)$")


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=200)
    brief: str = Field(default="", max_length=20000)
    tone: str = Field(default="", max_length=2000)
    screenplay: str = Field(default="", max_length=50000)
    characters: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    scenes: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    shots: list[dict[str, str | int]] = Field(default_factory=list, max_length=200)
    image_settings: dict[str, str] = Field(default_factory=dict)


def _owned(session, session_id, lock=False):
    actor = require_actor()
    query = select(AgentSession).where(
        AgentSession.id == session_id, AgentSession.user_id == actor.id
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if not row:
        raise HTTPException(404, "Agent 会话不存在。")
    if row.project_id:
        get_project(row.project_id)
    return row


def _serialize(row):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "mode": row.mode,
        "stage": row.stage,
        "stage_label": STAGE_LABELS.get(row.stage, row.stage),
        "status": row.status,
        "plan": deepcopy(row.plan),
        "messages": deepcopy(row.messages),
        "job": deepcopy(row.job),
        "stages": [
            {"id": stage, "label": STAGE_LABELS[stage], "active": stage == row.stage}
            for stage in STAGES
        ],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _advance(stage):
    try:
        return STAGES[min(STAGES.index(stage) + 1, len(STAGES) - 1)]
    except ValueError:
        return STAGES[0]


@router.get("/sessions")
def list_sessions(project_id: str | None = Query(None, max_length=100)):
    actor = require_actor()
    with Session(database.get_engine()) as session:
        query = select(AgentSession).where(AgentSession.user_id == actor.id)
        if project_id is not None and project_id:
            query = query.where(AgentSession.project_id == project_id)
        elif project_id == "":
            query = query.where(AgentSession.project_id.is_(None))
        row = session.scalar(query.order_by(AgentSession.updated_at.desc()).limit(1))
        return _serialize(row) if row else None


@router.post("/sessions", status_code=201)
def create_session(body: SessionInput):
    actor = require_actor()
    if body.project_id:
        get_project(body.project_id)
    row = AgentSession(
        id=uuid.uuid4().hex,
        user_id=actor.id,
        project_id=body.project_id or None,
        mode=body.mode,
        stage="brief",
        status="active",
        api_profile_id=body.api_profile_id,
        plan=empty_plan(),
        messages=[],
        job={"status": "idle", "progress": 0, "message": ""},
    )
    with Session(database.get_engine()) as session, session.begin():
        session.add(row)
        session.flush()
        return _serialize(row)


@router.get("/sessions/{session_id}")
def read_session(session_id: str):
    with Session(database.get_engine()) as session:
        return _serialize(_owned(session, session_id))


@router.post("/sessions/{session_id}/messages")
def message(session_id: str, body: MessageInput):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, lock=True)
        if row.status == "completed":
            raise HTTPException(409, "这个 Agent 会话已经完成，请新建会话继续创作。")
        content = body.content.strip()
        plan, reply = respond(content, row.plan, row.stage, row.mode)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row.plan = plan
        row.messages = [
            *row.messages,
            {"role": "user", "content": content, "stage": row.stage, "created_at": stamp},
            {"role": "assistant", "content": reply, "stage": row.stage, "created_at": stamp},
        ][-80:]
        return _serialize(row)


@router.post("/sessions/{session_id}/mode")
def update_mode(session_id: str, body: ModeInput):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, lock=True)
        if row.status == "completed":
            raise HTTPException(409, "这个 Agent 会话已经完成，请新建会话继续创作。")
        row.mode = body.mode
        return _serialize(row)


@router.post("/sessions/{session_id}/plan")
def update_plan(session_id: str, body: PlanPatch):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, lock=True)
        row.plan = body.model_dump()
        return _serialize(row)


@router.post("/sessions/{session_id}/confirm")
def confirm_stage(session_id: str):
    with Session(database.get_engine()) as session:
        row = _owned(session, session_id)
        if not row.project_id:
            project = ensure_project("", row.plan.get("title", "小梦创作项目"), row.plan.get("brief", ""))
            row.project_id = project["id"]
            session.commit()
        project_id, plan, stage = row.project_id, deepcopy(row.plan), row.stage
    apply_stage(project_id, plan, stage)
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, lock=True)
        row.stage = _advance(stage)
        if stage == "generation":
            row.status = "completed"
        return _serialize(row)


def _set_job(session_id, actor_id, **updates):
    with Session(database.get_engine()) as session, session.begin():
        row = session.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id, AgentSession.user_id == actor_id
            ).with_for_update()
        )
        if row:
            row.job = {**row.job, **updates}
            row.status = "active" if updates.get("status") in {"queued", "running"} else row.status


def _autonomous_job(session_id, actor: Actor):
    token = current_actor.set(actor)
    try:
        with Session(database.get_engine()) as session:
            row = _owned(session, session_id)
            project_id = row.project_id
            plan = deepcopy(row.plan)
            api_profile_id = row.api_profile_id
        if not project_id:
            project = ensure_project("", plan.get("title", "小梦创作项目"), plan.get("brief", ""))
            project_id = project["id"]
            with Session(database.get_engine()) as session, session.begin():
                row = _owned(session, session_id, lock=True)
                row.project_id = project_id
        _set_job(session_id, actor.id, status="running", progress=10, message="正在写入创作规划")
        for index, stage in enumerate(STAGES[:-1], 1):
            apply_stage(project_id, plan, stage)
            _set_job(session_id, actor.id, progress=10 + index * 12, message=f"已完成{STAGE_LABELS[stage]}")
        with Session(database.get_engine()) as session:
            project = get_project(project_id)
            frames = [frame for frame in project["frames"] if frame.get("marked") and frame.get("status") == "draft"]
        if frames:
            from .frames import GenerationInput, generate_frame

            for index, frame in enumerate(frames, 1):
                _set_job(
                    session_id,
                    actor.id,
                    progress=70 + int(index * 30 / len(frames)),
                    message=f"正在生成 {frame.get('title', '镜头')}（{index}/{len(frames)}）",
                )
                generate_frame(
                    project_id,
                    frame["id"],
                    GenerationInput(
                        title=frame.get("title", ""),
                        prompt=frame.get("prompt", ""),
                        system_prompt=frame.get("system_prompt", ""),
                        model=frame.get("model", get_settings()["default_model"]),
                        size=frame.get("size", get_settings()["default_size"]),
                        character_card_ids=frame.get("character_card_ids", []),
                        scene_card_ids=frame.get("scene_card_ids", []),
                        marked=True,
                        api_profile_id=api_profile_id,
                    ),
                )
        _set_job(session_id, actor.id, status="completed", progress=100, message="托管任务已完成")
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, lock=True)
            row.stage = "generation"
            row.status = "completed"
    except Exception:
        _set_job(session_id, actor.id, status="failed", message="托管执行失败，请查看弹窗后重试")
    finally:
        current_actor.reset(token)


@router.post("/sessions/{session_id}/run")
def run_session(session_id: str, background_tasks: BackgroundTasks):
    actor = require_actor()
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, lock=True)
        if row.job.get("status") in {"queued", "running"}:
            return _serialize(row)
        row.mode = "autonomous"
        row.job = {"status": "queued", "progress": 0, "message": "托管任务已排队"}
        background_tasks.add_task(_autonomous_job, session_id, actor)
        return _serialize(row)
