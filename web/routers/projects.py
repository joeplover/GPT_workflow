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
