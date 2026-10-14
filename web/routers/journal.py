"""Generation archive scoped to the same private/team projects as workbenches."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import database
from ..models import ImageGeneration, Project
from ..security import project_scope, require_actor

router = APIRouter(prefix="/api/generations", tags=["generation journal"])


def serialize(row, project_name):
    return {
        "id": row.id,
        "generation_key": row.generation_key,
        "project_id": row.project_id,
        "project_name": project_name,
        "frame_id": row.frame_id,
        "actor_id": row.actor_id,
        "status": row.status,
        "request": row.request,
        "image_url": row.image_url,
        "error": row.error,
        "historical": row.historical,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.get("")
def list_generations(
    project_id: str | None = None,
    frame_id: str | None = None,
    status: str | None = None,
    before: int | None = Query(None, ge=1),
    limit: int = Query(30, ge=1, le=100),
):
    if status and status not in {"generating", "succeeded", "failed", "interrupted"}:
        raise HTTPException(400, "无效的生成状态。")
    scope = project_scope(require_actor())
    with Session(database.get_engine()) as session, session.begin():
        if project_id and not session.scalar(
            select(Project.id).where(scope, Project.id == project_id)
        ):
            raise HTTPException(404, "项目不存在或无权访问。")
        session.execute(
            update(ImageGeneration)
            .where(
                ImageGeneration.project_id.in_(select(Project.id).where(scope)),
                ImageGeneration.status == "generating",
                ImageGeneration.expires_at < datetime.now(timezone.utc),
            )
            .values(
                status="interrupted",
                error="生成进程中断或超时，可重新生成。",
                finished_at=datetime.now(timezone.utc),
            )
        )
        query = (
            select(ImageGeneration, Project)
            .join(Project, Project.id == ImageGeneration.project_id)
            .where(scope)
        )
        for column, value in (
            (ImageGeneration.project_id, project_id),
            (ImageGeneration.frame_id, frame_id),
            (ImageGeneration.status, status),
        ):
            if value:
                query = query.where(column == value)
        if before:
            query = query.where(ImageGeneration.id < before)
        rows = session.execute(
            query.order_by(ImageGeneration.id.desc()).limit(limit + 1)
        ).all()
        items = [
            serialize(row, project.document.get("name", ""))
            for row, project in rows[:limit]
        ]
        return {
            "items": items,
            "next_before": items[-1]["id"] if len(rows) > limit else None,
        }


@router.get("/{record_id}")
def read_generation(record_id: int):
    with Session(database.get_engine()) as session:
        match = session.execute(
            select(ImageGeneration, Project)
            .join(Project, Project.id == ImageGeneration.project_id)
            .where(ImageGeneration.id == record_id, project_scope(require_actor()))
        ).first()
        if not match:
            raise HTTPException(404, "生成记录不存在或无权访问。")
        row, project = match
        return serialize(row, project.document.get("name", ""))
