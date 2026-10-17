"""Typed creative tools. Reuse the workbench's access checks and audit trail."""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..repository import get_project, load_projects, mutate_project, now_iso
from ..config import APP_PREFIX
from ..routers import frames, projects, management, journal
from ..routers.frames import FrameInput


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Args):
    pass


class Script(Args):
    screenplay_text: str = Field(max_length=50000)


class Card(Args):
    card_type: Literal["character", "scene"]
    card_id: str = ""
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=20000)


class CardId(Args):
    card_type: Literal["character", "scene"]
    card_id: str


class FrameId(Args):
    frame_id: str


class FramePatch(Args):
    frame_id: str
    title: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=20000)
    system_prompt: str | None = Field(default=None, max_length=20000)
    model: str | None = None
    size: str | None = None
    character_card_ids: list[str] | None = None
    scene_card_ids: list[str] | None = None
    marked: bool | None = None


class Generate(Args):
    frame_ids: list[str] = Field(min_length=1, max_length=200)


class FrameBatch(Args):
    frames: list[FrameInput] = Field(min_length=1, max_length=200)


class CardImage(CardId):
    frame_id: str


class History(Args):
    frame_id: str | None = None
    before: int | None = Field(default=None, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class Restore(FrameId):
    record_id: int


class Asset(Args):
    image_url: str = Field(min_length=1, max_length=1024)


# name -> schema, description, mutation, always require explicit confirmation
TOOLS = {
    "create_project": (management.ProjectInput, "按当前已讨论规划创建项目（若会话已有项目则修改其元信息）。", True, False),
    "get_project": (Empty, "查看当前项目、剧本、参考卡、镜头及其真实 ID。", False, False),
    "list_projects": (Empty, "列出有权访问的项目（不切换当前项目）。", False, False),
    "update_project": (management.ProjectInput, "修改项目名称、简介或归档状态。", True, False),
    "save_script": (Script, "保存已与用户讨论的剧本。", True, False),
    "upsert_card": (Card, "创建或修改角色卡/场景卡，修改时指定真实 card_id。", True, False),
    "delete_card": (CardId, "删除角色卡/场景卡，保留历史图片。", True, True),
    "create_frame": (frames.FrameInput, "创建镜头，可指定提示词、模型、尺寸和多选参考卡。", True, False),
    "create_frames": (FrameBatch, "批量创建已规划的分镜，每个镜头可独立指定提示词和参考卡。", True, False),
    "update_frame": (FramePatch, "局部修改镜头；省略字段保持不变，不自动重新生图。", True, False),
    "delete_frame": (FrameId, "删除镜头，保留生成历史。", True, True),
    "apply_cards": (frames.ApplyCardsInput, "给多个镜头应用角色卡与场景卡。", True, False),
    "generate_frames": (Generate, "生成或重新生成指定真实镜头 ID，沿用镜头参数，保留历史版本。", True, False),
    "set_card_image": (CardImage, "使用当前项目某个已生成镜头作为角色卡或场景卡参考图。可先创建专用设定图镜头并生成。", True, False),
    "remove_frame_image": (FrameId, "解除镜头当前图片，保留生成历史。", True, True),
    "remove_card_image": (CardId, "解除参考卡图片，保留生成历史。", True, True),
    "list_resources": (Empty, "查找用户或团队可用的角色卡、场景卡。", False, False),
    "import_resources": (management.ImportInput, "从有权限的项目导入角色卡/场景卡。", True, False),
    "generation_history": (History, "查询本项目生图日志、历史图片及完整提示词。支持分页。", False, False),
    "restore_frame_image": (Restore, "将同项目同镜头成功的历史图片恢复为当前图片。", True, False),
    "image_downloads": (Empty, "获取本项目当前图片的下载地址。", False, False),
    "purge_image": (Asset, "彻底移除图片文件及引用，不可撤销，必须单独确认。", True, True),
}


def definitions():
    return [{"type": "function", "function": {"name": name, "description": spec[1],
             "parameters": spec[0].model_json_schema()}} for name, spec in TOOLS.items()]


def validate_call(name, arguments):
    if name not in TOOLS:
        raise HTTPException(400, "小梦请求了未开放的工具。")
    return TOOLS[name][0].model_validate(arguments)


def needs_confirmation(decision, mode):
    if decision.get("action") != "tool":
        return mode == "guided"
    spec = TOOLS[decision["tool_name"]]
    return spec[3] or (mode == "guided" and spec[2])


def execute_tool(project_id, name, arguments, api_profile_id=""):
    args = validate_call(name, arguments)
    if name == "generate_frames" and not api_profile_id:
        # Agent sessions created before a profile was selected may have an empty
        # binding. Resolve the user's current selected profile at execution time.
        from .api_profiles import selected_profile_id
        api_profile_id = selected_profile_id()
    # Every project operation is bound to the session project, never an arbitrary URL.
    if name == "list_projects":
        return [{key: p.get(key) for key in ("id", "name", "synopsis")} for p in load_projects()]
    if name == "list_resources":
        return management.resources()
    if not project_id:
        return {"ok": False, "message": "当前会话尚未关联项目。请先与用户规划，再执行创建/保存操作。"}
    project = get_project(project_id)
    if name == "get_project":
        from .agent import project_context
        return project_context(project)
    if name in {"update_project", "create_project"}:
        management.metadata(project_id, args)
    elif name == "save_script":
        projects.parse_screenplay(project_id, args.screenplay_text)
    elif name == "upsert_card":
        if not args.card_id:
            projects.create_card(project_id, args.card_type, args.name, args.content, None)
        else:
            def update(p):
                card = _card(p, args)
                card.update(name=args.name, content=args.content, updated_at=now_iso())
            mutate_project(project_id, update, "agent.card.update")
    elif name in {"delete_card", "remove_card_image"}:
        projects.remove_card_resource(project_id, args.card_type, args.card_id, name == "remove_card_image")
    elif name == "create_frame":
        return frames.create_frame(project_id, args)
    elif name == "create_frames":
        added = [frames.new_frame(body) for body in args.frames]
        def append(p):
            if len(p["frames"]) + len(added) > frames.get_settings()["max_project_shots"]:
                raise HTTPException(400, "镜头数量已达到项目上限。")
            for body in args.frames:
                frames.validate_cards(p, body)
            for frame in added:
                frame["title"] = frame["title"].strip() or f"镜头 {len(p['frames']) + 1:02d}"
                p["frames"].append(frame)
            p["frames_initialized"] = True
        mutate_project(project_id, append, "agent.frames.create")
        return added
    elif name == "update_frame":
        frame = frames.find_frame(project, args.frame_id)
        patch = args.model_dump(exclude_none=True, exclude={"frame_id"})
        body = frames.FrameInput.model_validate({**frame, **patch})
        frames.validate_cards(project, body)
        return frames.save_frame(project_id, args.frame_id, body)
    elif name == "delete_frame":
        frames.delete_frame(project_id, args.frame_id)
    elif name == "apply_cards":
        frames.apply_cards(project_id, args)
    elif name == "generate_frames":
        results = []
        for frame_id in dict.fromkeys(args.frame_ids):
            frame = frames.find_frame(project, frame_id)
            try:
                result = frames.generate_frame(project_id, frame_id, frames.GenerationInput.model_validate(
                    {**frame, "api_profile_id": api_profile_id}))
                results.append({"frame_id": frame_id, "ok": True, "image_url": result.get("image_url")})
            except HTTPException as exc:
                results.append({"frame_id": frame_id, "ok": False, "message": str(exc.detail)})
        return {"ok": all(r["ok"] for r in results), "frames": results}
    elif name == "set_card_image":
        def attach(p):
            frame = frames.find_frame(p, args.frame_id)
            if not frame.get("image_url"):
                raise HTTPException(400, "该镜头还没有可用的图片。")
            _card(p, args).update(image_url=frame["image_url"], updated_at=now_iso())
        mutate_project(project_id, attach, "agent.card.image")
    elif name == "remove_frame_image":
        frames.remove_frame_image(project_id, args.frame_id)
    elif name == "import_resources":
        management.import_resources(project_id, args)
    elif name == "generation_history":
        return journal.list_generations(project_id=project_id, frame_id=args.frame_id,
                                        before=args.before, limit=args.limit)
    elif name == "restore_frame_image":
        record = journal.read_generation(args.record_id)
        if (record["project_id"] != project_id or record["frame_id"] != args.frame_id
                or record["status"] != "succeeded" or record["image_deleted"] or not record["image_url"]):
            raise HTTPException(400, "只能恢复当前镜头尚未删除的成功生成记录。")
        def restore(p):
            frame = frames.find_frame(p, args.frame_id)
            frames.require_idle(frame)
            frame.update(image_url=record["image_url"], status="ready", error="", updated_at=now_iso())
        mutate_project(project_id, restore, "agent.frame.restore")
    elif name == "image_downloads":
        return [{"frame_id": f["id"], "title": f["title"], "image_url": f["image_url"],
                 "download_url": (f["image_url"] if f["image_url"].startswith(APP_PREFIX + "/")
                                  else APP_PREFIX + f["image_url"]) + "?download=true"}
                for f in project["frames"] if f.get("image_url")]
    elif name == "purge_image":
        from .assets import purge_image
        return purge_image(project_id, args.image_url)
    return {"ok": True, "message": TOOLS[name][1] + " 操作完成。"}


def _card(project, args):
    card = next((c for c in project[args.card_type + "_cards"] if c["id"] == args.card_id), None)
    if card is None:
        raise HTTPException(404, "参考卡不存在。")
    return card
