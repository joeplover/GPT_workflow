"""Stable storyboard slots with independent, concurrency-safe generation."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings, resolve_runtime_settings
from ..repository import get_project, mutate_project, now_iso
from ..services.images import (
    build_reference_collage,
    relay_edit_raw,
    relay_generate_raw,
)
from ..services.storyboard import (
    build_composed_prompt,
    get_card_image_paths,
    normalize_generation_args,
)

router = APIRouter(prefix="/api/projects/{project_id}/frames", tags=["storyboard"])
logger = logging.getLogger(__name__)


class FrameInput(BaseModel):
    title: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=20000)
    system_prompt: str = Field(default="", max_length=20000)
    model: str = "gpt-image-2"
    size: str = "2048x1152"
    character_card_ids: list[str] = Field(default_factory=list)
    scene_card_ids: list[str] = Field(default_factory=list)
    marked: bool = False


class GenerationInput(FrameInput):
    api_key: str = ""
    base_url: str = ""


class ApplyCardsInput(BaseModel):
    frame_ids: list[str] = Field(default_factory=list)
    character_card_ids: list[str] = Field(default_factory=list)
    scene_card_ids: list[str] = Field(default_factory=list)


def find_frame(project: dict[str, Any], frame_id: str) -> dict[str, Any]:
    for frame in project["frames"]:
        if frame["id"] == frame_id:
            return frame
    raise HTTPException(404, "镜头不存在。")


def is_running(frame: dict[str, Any]) -> bool:
    if frame.get("status") != "generating":
        return False
    # A crashed/restarted worker must not leave a frame permanently locked.
    return datetime.fromisoformat(frame["lease_until"]) > datetime.now(timezone.utc)


def require_idle(frame: dict[str, Any]) -> None:
    if is_running(frame):
        raise HTTPException(409, "这个镜头正在生成，请等待完成后再修改。")


@router.get("")
def list_frames(project_id: str) -> list[dict[str, Any]]:
    project = get_project(project_id)
    expired = any(
        f.get("status") == "generating" and not is_running(f) for f in project["frames"]
    )
    if expired:

        def recover(current):
            for frame in current["frames"]:
                if frame.get("status") == "generating" and not is_running(frame):
                    frame.update(
                        status="failed",
                        error="生成已中断或超时，可以重新生成。",
                        generation_id=None,
                    )

        project = mutate_project(project_id, recover, "frame.recover")
    return project["frames"]


def new_frame(body: FrameInput) -> dict[str, Any]:
    body = body.model_copy(update=normalize_generation_args(body.model, body.size))
    return {
        **body.model_dump(),
        "id": uuid.uuid4().hex,
        "status": "draft",
        "image_url": None,
        "error": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "versions": [],
    }


@router.post("/initialize")
def initialize_frames(project_id: str) -> list[dict[str, Any]]:
    """Start a storyboard once; intentionally emptied projects stay empty."""

    def initialize(project):
        if project.get("frames_initialized"):
            return
        if not project["frames"]:
            settings = get_settings()
            frame = new_frame(
                FrameInput(
                    title="镜头 01",
                    model=settings["default_model"],
                    size=settings["default_size"],
                )
            )
            project["frames"].append(frame)
        project["frames_initialized"] = True

    return mutate_project(project_id, initialize, "frame.initialize")["frames"]


@router.post("")
def create_frame(project_id: str, body: FrameInput) -> dict[str, Any]:
    frame = new_frame(body)

    def append(project):
        if len(project["frames"]) >= get_settings()["max_project_shots"]:
            raise HTTPException(400, "镜头数量已达到项目上限。")
        frame["title"] = body.title.strip() or f"镜头 {len(project['frames']) + 1:02d}"
        project["frames"].append(frame)
        project["frames_initialized"] = True

    mutate_project(project_id, append, "frame.create")
    return frame


@router.delete("/{frame_id}")
def delete_frame(project_id: str, frame_id: str) -> list[dict[str, Any]]:
    def remove(project):
        frame = find_frame(project, frame_id)
        require_idle(frame)
        project["frames"].remove(frame)
        project["frames_initialized"] = True
        workspace = project.get("workspace", {})
        if workspace.get("selected_frame_id") == frame_id:
            workspace["selected_frame_id"] = (
                project["frames"][0]["id"] if project["frames"] else ""
            )

    # Keep image files: references or other records may still use them.
    return mutate_project(project_id, remove, "frame.delete")["frames"]


@router.post("/apply-cards")
def apply_cards(project_id: str, body: ApplyCardsInput) -> list[dict[str, Any]]:
    """Apply the selected reference cards to several frames in one transaction."""
    if not body.frame_ids:
        raise HTTPException(400, "请至少选择一个要应用的镜头。")

    def change(project):
        character_ids = set(body.character_card_ids)
        scene_ids = set(body.scene_card_ids)
        valid_character_ids = {card["id"] for card in project["character_cards"]}
        valid_scene_ids = {card["id"] for card in project["scene_cards"]}
        unknown = (character_ids - valid_character_ids) | (scene_ids - valid_scene_ids)
        if unknown:
            raise HTTPException(400, "选择的角色卡或场景卡不存在。")
        selected_frames = set(body.frame_ids)
        for frame in project["frames"]:
            if frame["id"] not in selected_frames:
                continue
            require_idle(frame)
            frame["character_card_ids"] = list(dict.fromkeys(body.character_card_ids))
            frame["scene_card_ids"] = list(dict.fromkeys(body.scene_card_ids))
            frame["updated_at"] = now_iso()
        found = {
            frame["id"] for frame in project["frames"] if frame["id"] in selected_frames
        }
        if found != selected_frames:
            raise HTTPException(404, "部分镜头不存在。")

    project = mutate_project(project_id, change, "cards.apply")
    return [frame for frame in project["frames"] if frame["id"] in set(body.frame_ids)]


@router.post("/{frame_id}")
def save_frame(project_id: str, frame_id: str, body: FrameInput) -> dict[str, Any]:
    # Persist incomplete settings as drafts too; generation still validates them.

    def change(project):
        frame = find_frame(project, frame_id)
        require_idle(frame)
        frame.update(body.model_dump(), updated_at=now_iso())

    return find_frame(mutate_project(project_id, change, "frame.save"), frame_id)


@router.post("/{frame_id}/generate")
def generate_frame(
    project_id: str, frame_id: str, body: GenerationInput
) -> dict[str, Any]:
    if not body.prompt.strip():
        raise HTTPException(400, "请填写这个镜头的提示词。")
    validated = normalize_generation_args(body.model, body.size)
    body = body.model_copy(update=validated)
    settings = resolve_runtime_settings(body.api_key, body.base_url)
    if not settings["api_key"]:
        raise HTTPException(400, "请填写 API Key，或配置服务端 Key。")
    generation_id = uuid.uuid4().hex

    def claim(project):
        frame = find_frame(project, frame_id)
        require_idle(frame)
        frame.update(
            body.model_dump(exclude={"api_key", "base_url", "marked"}),
            status="generating",
            error="",
            generation_id=generation_id,
            updated_at=now_iso(),
            lease_until=(
                datetime.now(timezone.utc)
                + timedelta(seconds=max(settings["request_timeout"] * 6 + 120, 600))
            ).isoformat(),
        )

    project = mutate_project(project_id, claim, "frame.generate.start")
    try:
        paths = get_card_image_paths(
            project, body.character_card_ids, body.scene_card_ids
        )
        composed = build_composed_prompt(
            project,
            body.prompt,
            body.character_card_ids,
            body.scene_card_ids,
            has_reference_images=bool(paths),
            system_prompt=body.system_prompt,
        )
        arguments = {
            "prompt": composed,
            **validated,
            **{
                key: settings[key]
                for key in (
                    "api_key",
                    "base_url",
                    "verify_ssl",
                    "use_env_proxy",
                    "request_timeout",
                )
            },
        }
        if paths:
            result = relay_edit_raw(
                **arguments, image_path=build_reference_collage(paths), mask_path=None
            )
        else:
            result = relay_generate_raw(**arguments)

        def complete(current):
            frame = find_frame(current, frame_id)
            if frame.get("generation_id") != generation_id:
                return
            frame["versions"].insert(
                0,
                {
                    "image_url": result["image_url"],
                    "prompt": body.prompt,
                    "system_prompt": body.system_prompt,
                    "model": validated["model"],
                    "size": validated["size"],
                    "composed_prompt": composed,
                    "created_at": now_iso(),
                },
            )
            frame["versions"] = frame["versions"][:20]
            frame.update(
                image_url=result["image_url"],
                status="ready",
                error="",
                generation_id=None,
                generated_prompt=body.prompt,
                generated_system_prompt=body.system_prompt,
                updated_at=now_iso(),
            )

        return find_frame(
            mutate_project(project_id, complete, "frame.generate.complete"), frame_id
        )
    except Exception as exc:
        error = (
            str(exc.detail)
            if isinstance(exc, HTTPException)
            else "生成失败，请检查接口配置或稍后重试。"
        )
        logger.exception("Storyboard generation failed for frame %s", frame_id)

        def fail(current):
            frame = find_frame(current, frame_id)
            if frame.get("generation_id") == generation_id:
                frame.update(
                    status="failed",
                    error=error,
                    generation_id=None,
                    updated_at=now_iso(),
                )

        mutate_project(project_id, fail, "frame.generate.fail")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, error) from exc
