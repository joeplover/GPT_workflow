"""Configuration, readiness and diagnostics."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings, size_presets
from ..database import get_engine
from ..repository import get_project

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/config")
def read_config() -> Dict[str, Any]:
    settings = get_settings()
    return {
        "base_url": settings["base_url"],
        "default_model": settings["default_model"],
        "default_size": settings["default_size"],
        "verify_ssl": settings["verify_ssl"],
        "use_env_proxy": settings["use_env_proxy"],
        "request_timeout": settings["request_timeout"],
        "max_project_shots": settings["max_project_shots"],
        "has_api_key": bool(settings["api_key"]),
        "supported_modes": ["generate", "edit"],
        "suggested_models": list(
            dict.fromkeys(
                [
                    settings["default_model"],
                    "gpt-image-2",
                    "gpt-image-1",
                    "gpt-image-1-mini",
                    "dall-e-3",
                ]
            )
        ),
        "suggested_sizes": [item["size"] for item in size_presets()],
        "size_presets": size_presets(),
    }


@router.get("/debug/state")
def debug_state(project_id: str = "") -> Dict[str, Any]:
    """诊断端点：查看当前项目卡片、上传文件等状态"""
    project_info = None
    if project_id:
        try:
            project = get_project(project_id)
            project_info = {
                "name": project.get("name"),
                "character_cards_count": len(project.get("character_cards", [])),
                "scene_cards_count": len(project.get("scene_cards", [])),
                "shots_count": len(project.get("shots", [])),
                "character_cards": [
                    {
                        "name": c["name"],
                        "has_image": bool(c.get("image_url")),
                        "image_url": c.get("image_url"),
                    }
                    for c in project.get("character_cards", [])
                ],
                "scene_cards": [
                    {
                        "name": c["name"],
                        "has_image": bool(c.get("image_url")),
                        "image_url": c.get("image_url"),
                    }
                    for c in project.get("scene_cards", [])
                ],
            }
        except HTTPException:
            raise
    return {
        "project_id": project_id,
        "project": project_info,
    }


@router.get("/health")
def health() -> Dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc
    return {"status": "ok", "database": "postgresql"}
