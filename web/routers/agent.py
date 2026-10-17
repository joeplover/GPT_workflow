"""Persistent, user-scoped conversations and model-directed project tools."""

from copy import deepcopy
import logging
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..models import AgentSession
from ..repository import ensure_project, get_project, now_iso
from ..security import Actor, current_actor, require_actor
from ..services.agent import STAGES, STAGE_LABELS, apply_stage, empty_plan, project_context, run_agent
from ..services.agent_tools import execute_tool, TOOLS
from ..errors import describe_error, save_exception

router = APIRouter(prefix="/api/agent", tags=["xiao meng agent"])
logger = logging.getLogger(__name__)


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(default="", max_length=100)
    mode: str = Field(default="guided", pattern="^(guided|autonomous)$")
    api_profile_id: str = Field(default="", max_length=100)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20000)
    background: bool = False


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
    memory: str = Field(default="", max_length=20000)


def _owned(session, session_id, lock=False):
    query = select(AgentSession).where(AgentSession.id == session_id, AgentSession.user_id == require_actor().id)
    row = session.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, "Agent 会话不存在。")
    if row.project_id:
        get_project(row.project_id)
    return row


def _idle(row):
    if row.job.get("status") in {"queued", "running"}:
        raise HTTPException(409, "小梦正在执行当前操作，请完成后再修改。")


def _serialize(row):
    return {
        "title": _title(row),
        "id": row.id, "project_id": row.project_id, "mode": row.mode,
        "stage": row.stage, "stage_label": STAGE_LABELS.get(row.stage, row.stage),
        "status": row.status, "plan": deepcopy(row.plan), "messages": deepcopy(row.messages),
        "job": deepcopy(row.job), "decision": deepcopy(row.job.get("decision", {})),
        "stages": [{"id": stage, "label": STAGE_LABELS[stage], "active": stage == row.stage} for stage in STAGES],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _title(row):
    return (row.plan.get("title") or next((m["content"] for m in row.messages if m.get("role") == "user"), "新会话"))[:80]


def _append(row, role, content):
    row.messages = [*row.messages, {"role": role, "content": content, "stage": row.stage, "created_at": now_iso()}]


@router.get("/sessions")
def list_sessions(project_id: str | None = Query(None, max_length=100)):
    with Session(database.get_engine()) as session:
        query = select(AgentSession).where(AgentSession.user_id == require_actor().id)
        if project_id:
            get_project(project_id)
            query = query.where(AgentSession.project_id == project_id)
        elif project_id == "":
            query = query.where(AgentSession.project_id.is_(None))
        rows = session.scalars(query.order_by(AgentSession.updated_at.desc(), AgentSession.id.desc())).all()
        return [{"id": row.id, "title": _title(row), "project_id": row.project_id,
                 "status": row.job.get("status", "idle"),
                 "updated_at": row.updated_at.isoformat() if row.updated_at else None} for row in rows]


@router.post("/sessions", status_code=201)
def create_session(body: SessionInput):
    if body.project_id:
        get_project(body.project_id)
    row = AgentSession(
        id=uuid.uuid4().hex, user_id=require_actor().id, project_id=body.project_id or None,
        mode=body.mode, stage="brief", status="active", api_profile_id=body.api_profile_id,
        plan=empty_plan(), messages=[], job={"status": "idle", "progress": 0, "message": ""},
    )
    with Session(database.get_engine()) as session, session.begin():
        session.add(row)
        session.flush()
        return _serialize(row)


@router.get("/sessions/{session_id}")
def read_session(session_id: str):
    with Session(database.get_engine()) as session:
        return _serialize(_owned(session, session_id))


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        _idle(row)
        session.delete(row)
    return {"ok": True}


def _execute(project_id, decision, plan, api_profile_id):
    if decision["action"] == "tool":
        data = execute_tool(project_id, decision["tool_name"], decision.get("arguments", {}), api_profile_id)
        ok = data.get("ok", True) if isinstance(data, dict) else True
        return {"ok": ok, "message": "工具执行完成" if ok else "工具执行未完成", "result": data}, project_context(get_project(project_id)) if project_id else {}
    if decision["action"] == "save":
        if decision["focus_stage"] == "generation":
            raise HTTPException(400, "生图请使用生成操作并指定镜头。")
        project = apply_stage(project_id, plan, decision["focus_stage"])
        return {"ok": True, "message": f"已写入{STAGE_LABELS[decision['focus_stage']]}"}, project_context(project)
    if decision["action"] != "generate":
        raise HTTPException(400, "当前没有待执行的项目操作。")
    from .frames import GenerationInput, generate_frame
    if not api_profile_id:
        from ..services.api_profiles import selected_profile_id
        api_profile_id = selected_profile_id()

    project = get_project(project_id)
    selected = set(decision.get("frame_ids", []))
    available = {f["id"] for f in project["frames"]}
    if not selected or selected - available:
        raise HTTPException(400, "请指定实际存在的镜头后生成。")
    results = []
    for frame in project["frames"]:
        if frame["id"] not in selected:
            continue
        try:
            generated = generate_frame(project_id, frame["id"], GenerationInput(
                **{key: frame[key] for key in ("title", "prompt", "system_prompt", "model", "size",
                   "character_card_ids", "scene_card_ids") if key in frame}, api_profile_id=api_profile_id,
            ))
            results.append({"frame_id": frame["id"], "ok": bool(generated and generated.get("status") == "ready")})
        except HTTPException as exc:
            results.append({"frame_id": frame["id"], "ok": False, "message": str(exc.detail)})
    ok = all(item["ok"] for item in results)
    return {"ok": ok, "message": "所选镜头已生成" if ok else "部分镜头生成失败，请检查并调整", "frames": results}, project_context(get_project(project_id))


def _turn(session_id, text):
    with Session(database.get_engine()) as session:
        row = _owned(session, session_id)
        plan, stage, mode = deepcopy(row.plan), row.stage, row.mode
        if mode == "autonomous" and not row.job.get("execution_authorized"):
            mode = "guided"
        messages, project_id, profile = deepcopy(row.messages), row.project_id, row.api_profile_id
        previous_observations = deepcopy(row.job.get("observations", []))
        if previous_observations:
            messages.append({"role": "system", "content": "上次工具执行结果", "results": previous_observations})

    def execute(decision, current_plan):
        nonlocal project_id
        is_read = decision.get("action") == "tool" and not TOOLS[decision["tool_name"]][2]
        if not project_id and not is_read:
            project_id = ensure_project("", current_plan.get("title") or "小梦创作项目", current_plan.get("brief", ""))["id"]
            with Session(database.get_engine()) as session, session.begin():
                _owned(session, session_id, True).project_id = project_id
        return _execute(project_id, decision, current_plan, profile)

    def checkpoint(state):
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, True)
            row.plan, row.stage = deepcopy(state["plan"]), state["stage"]
            row.job = {**row.job, "decision": deepcopy(state["decision"]),
                       "pending_confirmation": state.get("pending", False),
                       "observations": deepcopy(state["observations"]),
                       "progress": min(95, 10 + state["steps"] * 10)}

    try:
        result = run_agent(text, plan, stage, mode, messages,
                           project_context(get_project(project_id)) if project_id else {}, execute, checkpoint)
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, True)
            row.status = "active"
            _append(row, "assistant", result["decision"]["reply"])
            row.job = {**row.job, "status": "completed", "progress": 100, "message": "本轮处理完成"}
            return _serialize(row)
    except Exception as exc:
        info = describe_error(exc)
        message = info.message
        logger.exception("Agent turn failed: %s", session_id)
        request_id = save_exception(SimpleNamespace(
            state=SimpleNamespace(actor_id=require_actor().id),
            scope={"route": SimpleNamespace(path="/api/agent/sessions/{session_id}/messages")}, method="POST"), exc, info)
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, True)
            row.job = {**row.job, "status": "failed", "message": message, "error_code": info.code,
                       "request_id": request_id, "pending_confirmation": False}
            _append(row, "assistant", message)
        raise


def _background_turn(session_id, actor: Actor, text):
    token = current_actor.set(actor)
    try:
        _turn(session_id, text)
    except Exception:
        pass  # _turn persists and logs the failure, visible through polling.
    finally:
        current_actor.reset(token)


@router.post("/sessions/{session_id}/messages")
def message(session_id: str, body: MessageInput, background_tasks: BackgroundTasks):
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "请输入消息内容。")
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        _idle(row)
        row.status = "active"
        _append(row, "user", text)
        row.job = {"status": "running", "progress": 0, "message": "小梦正在思考",
                   "execution_authorized": row.job.get("execution_authorized", False),
                   "observations": deepcopy(row.job.get("observations", []))}
        autonomous = row.mode == "autonomous" or body.background
        snapshot = _serialize(row)
    if autonomous:
        background_tasks.add_task(_background_turn, session_id, require_actor(), text)
        return snapshot
    return _turn(session_id, text)


@router.post("/sessions/{session_id}/mode")
def update_mode(session_id: str, body: ModeInput):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        _idle(row)
        row.mode, row.status = body.mode, "active"
        row.job = {**row.job, "execution_authorized": False}
        return _serialize(row)


@router.post("/sessions/{session_id}/plan")
def update_plan(session_id: str, body: PlanPatch):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        _idle(row)
        patch = body.model_dump(exclude={"memory"} if "memory" not in body.model_fields_set else set())
        row.plan = {**row.plan, **patch}
        return _serialize(row)


@router.post("/sessions/{session_id}/confirm")
def confirm_stage(session_id: str, background_tasks: BackgroundTasks, background: bool = False):
    if background:
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, True)
            _idle(row)
            if not row.job.get("pending_confirmation"):
                raise HTTPException(409, "当前没有待确认操作。")
            row.job = {**row.job, "status": "queued", "message": "已确认，正在执行"}
            snapshot = _serialize(row)
        background_tasks.add_task(_background_confirm, session_id, require_actor())
        return snapshot
    return _confirm(session_id)


def _background_confirm(session_id, actor):
    token = current_actor.set(actor)
    try:
        _confirm(session_id, queued=True)
    except Exception:
        logger.exception("Agent confirmation failed: %s", session_id)
    finally:
        current_actor.reset(token)


def _confirm(session_id, queued=False):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        if not queued:
            _idle(row)
        decision = deepcopy(row.job.get("decision", {}))
        if not row.job.get("pending_confirmation") or decision.get("confirmed"):
            raise HTTPException(409, "当前没有待确认操作，请先告诉小梦想做什么。")
        if not row.project_id:
            row.project_id = ensure_project("", row.plan.get("title") or "小梦创作项目", row.plan.get("brief", ""))["id"]
        project_id, plan, profile = row.project_id, deepcopy(row.plan), row.api_profile_id
        row.job = {**row.job, "status": "running", "message": "正在执行已确认操作"}
    try:
        result, _ = _execute(project_id, decision, plan, profile)
    except Exception as exc:
        with Session(database.get_engine()) as session, session.begin():
            row = _owned(session, session_id, True)
            info = describe_error(exc)
            row.job = {**row.job, "status": "failed", "message": info.message, "error_code": info.code}
            _append(row, "assistant", info.message)
        raise
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        if decision.get("tool_call"):
            result["tool_call"] = decision["tool_call"]
        row.job = {**row.job, "status": "completed" if result["ok"] else "failed",
                   "decision": {**decision, "confirmed": True}, "pending_confirmation": False,
                   "message": result["message"], "observations": [result]}
        _append(row, "assistant", result["message"] + "。你可以继续修改或告诉我下一步。")
        snapshot = _serialize(row)
        if queued and decision.get("action") == "tool" and result["ok"]:
            row.job = {**row.job, "status": "running", "message": "小梦正在分析执行结果"}
    if queued and decision.get("action") == "tool" and result["ok"]:
        return _turn(session_id, "用户已确认上一步，请根据真实工具结果继续；协作模式下下一次写入仍需确认。")
    return snapshot


@router.post("/sessions/{session_id}/run")
def run_session(session_id: str, background_tasks: BackgroundTasks):
    with Session(database.get_engine()) as session, session.begin():
        row = _owned(session, session_id, True)
        _idle(row)
        row.mode, row.status = "autonomous", "active"
        row.job = {**row.job, "status": "queued", "progress": 0, "message": "托管任务已排队", "pending_confirmation": False}
        row.job = {**row.job, "execution_authorized": True}
        text = "请继续完成用户最新目标，自主选择下一步工具，目标完成或需要用户补充时停止。"
        _append(row, "user", "开始托管：" + text)
        background_tasks.add_task(_background_turn, session_id, require_actor(), text)
        return _serialize(row)
