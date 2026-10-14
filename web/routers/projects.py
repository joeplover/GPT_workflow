"""Project, reference card and screenplay endpoints."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Query
from pydantic import BaseModel, Field, ConfigDict

from ..repository import (
    ensure_project,
    get_project,
    load_projects,
    mutate_project,
    normalize_project,
    now_iso,
    list_project_events,
)
from ..services.images import path_to_upload_url, save_upload_file
from ..services.storyboard import parse_screenplay_to_plan

router = APIRouter(prefix="/api", tags=["projects"])


class CardDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_type: str = "character"
    name: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=20000)


class SingleImageDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default="", max_length=20000)
    system_prompt: str = Field(default="", max_length=20000)
    model: str = Field(default="", max_length=200)
    size: str = Field(default="", max_length=100)
    project_name: str = Field(default="", max_length=200)
    character_card_ids: list[str] = Field(default_factory=list)
    scene_card_ids: list[str] = Field(default_factory=list)
    character_name: str = Field(default="", max_length=200)
    character_content: str = Field(default="", max_length=20000)
    scene_name: str = Field(default="", max_length=200)
    scene_content: str = Field(default="", max_length=20000)


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_frame_id: str | None = None
    library_selection: list[str] | None = None
    base_url: str | None = Field(default=None, max_length=2000)
    card_draft: CardDraftInput | None = None
    single_image_draft: SingleImageDraftInput | None = None


@router.post("/projects/{project_id}/workspace")
def save_workspace(project_id: str, body: WorkspaceInput) -> dict[str, Any]:
    def change(project):
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if "selected_frame_id" in updates and updates["selected_frame_id"]:
            if updates["selected_frame_id"] not in {f["id"] for f in project["frames"]}:
                raise HTTPException(400, "选择的镜头不存在。")
        if "library_selection" in updates:
            valid = {
                c["id"]
                for key in ("character_cards", "scene_cards")
                for c in project[key]
            }
            updates["library_selection"] = list(
                dict.fromkeys(c for c in updates["library_selection"] if c in valid)
            )
        if "single_image_draft" in updates:
            for kind in ("character", "scene"):
                valid_ids = {c["id"] for c in project[f"{kind}_cards"]}
                field = f"{kind}_card_ids"
                if field not in updates["single_image_draft"]:
                    continue
                updates["single_image_draft"][field] = [
                    c for c in updates["single_image_draft"][field] if c in valid_ids
                ]
        project.setdefault("workspace", {}).update(updates)

    return mutate_project(project_id, change, "workspace.save").get("workspace", {})


@router.get("/projects/{project_id}/events")
def read_events(
    project_id: str,
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1),
) -> list[dict[str, Any]]:
    return list_project_events(project_id, limit, before)


@router.get("/projects")
def read_projects() -> List[Dict[str, Any]]:
    return [normalize_project(item) for item in load_projects()]


@router.get("/projects/{project_id}")
def read_project(project_id: str) -> Dict[str, Any]:
    return get_project(project_id)


@router.post("/projects")
def create_project(
    name: str = Form(..., max_length=200),
    synopsis: str = Form("", max_length=20000),
    team_id: str = Form(""),
) -> Dict[str, Any]:
    return ensure_project(
        project_id="", project_name=name, synopsis=synopsis, team_id=team_id or None
    )


@router.post("/projects/{project_id}/cards")
def create_card(
    project_id: str,
    card_type: str = Form(...),
    name: str = Form(...),
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    if card_type not in {"character", "scene"}:
        raise HTTPException(
            status_code=400, detail="card_type must be character or scene."
        )
    get_project(project_id)
    key = "character_cards" if card_type == "character" else "scene_cards"

    image_url: Optional[str] = None
    if image and image.filename:
        saved_path = save_upload_file(image)
        if saved_path:
            image_url = path_to_upload_url(saved_path)

    card = {
        "id": uuid.uuid4().hex,
        "name": name.strip(),
        "content": content.strip(),
        "image_url": image_url,
        "created_at": now_iso(),
    }
    return mutate_project(
        project_id, lambda current: current[key].append(card), "card.create"
    )


def remove_card_resource(
    project_id: str, card_type: str, card_id: str, image_only: bool
):
    if card_type not in {"character", "scene"}:
        raise HTTPException(400, "无效的参考卡类型。")

    def change(project):
        from .frames import require_idle

        key = f"{card_type}_cards"
        card = next((c for c in project[key] if c["id"] == card_id), None)
        if card is None:
            raise HTTPException(404, "参考卡不存在。")
        field = f"{card_type}_card_ids"
        for frame in project["frames"]:
            if card_id in frame.get(field, []):
                require_idle(frame)
                if not image_only:
                    frame[field] = [value for value in frame[field] if value != card_id]
                    frame["updated_at"] = now_iso()
        if image_only:
            card["image_url"] = None
            card["updated_at"] = now_iso()
        else:
            project[key].remove(card)
            workspace = project.get("workspace", {})
            workspace["library_selection"] = [
                value
                for value in workspace.get("library_selection", [])
                if value != card_id
            ]
            draft = workspace.get("single_image_draft", {})
            if field in draft:
                draft[field] = [value for value in draft[field] if value != card_id]

    # Physical files and generation snapshots remain available in the journal/audit.
    return mutate_project(
        project_id, change, "card.image.remove" if image_only else "card.delete"
    )


@router.delete("/projects/{project_id}/cards/{card_type}/{card_id}")
def delete_card(project_id: str, card_type: str, card_id: str):
    return remove_card_resource(project_id, card_type, card_id, False)


@router.delete("/projects/{project_id}/cards/{card_type}/{card_id}/image")
def remove_card_image(project_id: str, card_type: str, card_id: str):
    return remove_card_resource(project_id, card_type, card_id, True)


@router.delete("/projects/{project_id}/shots/{shot_id}")
def remove_shot(project_id: str, shot_id: str):
    def change(project):
        shot = next((s for s in project["shots"] if s["id"] == shot_id), None)
        if shot is None:
            raise HTTPException(404, "图片记录不存在。")
        project["shots"].remove(shot)
        for index, item in enumerate(project["shots"], 1):
            item["shot_number"] = index

    return mutate_project(project_id, change, "image.remove")


@router.post("/projects/{project_id}/screenplay")
def parse_screenplay(
    project_id: str,
    screenplay_text: str = Form(...),
) -> Dict[str, Any]:
    plan = parse_screenplay_to_plan(screenplay_text)

    def change(project: Dict[str, Any]) -> None:
        project["screenplay_text"] = screenplay_text.strip()
        project["shot_plan"] = plan

    return mutate_project(project_id, change, "screenplay.save")
