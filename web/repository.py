"""Transactional project persistence; mutations lock only the affected project."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import get_engine
from .models import Project, ProjectEvent
from .security import current_actor, project_scope, require_project_access


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_project(project: dict[str, Any]) -> dict[str, Any]:
    for key in ("shots", "shot_plan", "character_cards", "scene_cards", "frames"):
        project.setdefault(key, [])
    project.setdefault("name", "Untitled project")
    project.setdefault("synopsis", "")
    project.setdefault("screenplay_text", "")
    project.setdefault("created_at", now_iso())
    project.setdefault("updated_at", now_iso())
    return project


def load_projects() -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        query = select(Project).order_by(Project.updated_at.desc(), Project.id.desc())
        if actor := current_actor.get():
            query = query.where(project_scope(actor))
        return [project_document(row) for row in session.scalars(query)]


def project_document(row: Project) -> dict[str, Any]:
    document = normalize_project(deepcopy(row.document))
    document.update(owner_id=row.owner_id, team_id=row.team_id)
    return document


def get_project(project_id: str) -> dict[str, Any]:
    with Session(get_engine()) as session:
        row = session.get(Project, project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        require_project_access(session, row)
        return project_document(row)


def mutate_project(
    project_id: str,
    change: Callable[[dict[str, Any]], None],
    operation: str = "project.update",
) -> dict[str, Any]:
    with Session(get_engine()) as session, session.begin():
        from .services.assets import assert_available, lock_assets

        lock_assets(session)
        row = session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        require_project_access(session, row)
        document = normalize_project(deepcopy(row.document))
        before = deepcopy(document)
        change(document)
        assert_available(session, document)
        changes = document_changes(before, document)
        if changes:
            session.add(
                ProjectEvent(
                    project_id=project_id,
                    operation=operation,
                    changes=changes,
                    actor_id=current_actor.get().id if current_actor.get() else None,
                )
            )
        document["updated_at"] = now_iso()
        row.document = document
        row.updated_at = func.now()
        document = deepcopy(document)
        document.update(owner_id=row.owner_id, team_id=row.team_id)
        return document


def document_changes(
    before: Any, after: Any, path: list[str] | None = None
) -> list[dict[str, Any]]:
    """Record changed values, keeping removed content without duplicating a project."""
    path = path or []
    changes = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            if key == "updated_at":
                continue
            if key in before and key in after:
                changes.extend(document_changes(before[key], after[key], path + [key]))
            else:
                changes.append(
                    {
                        "path": path + [key],
                        "before_exists": key in before,
                        "after_exists": key in after,
                        "before": before.get(key),
                        "after": after.get(key),
                    }
                )
    elif (
        isinstance(before, list)
        and isinstance(after, list)
        and all(
            isinstance(item, dict) and isinstance(item.get("id"), str)
            for item in before + after
        )
    ):
        old = {item["id"]: item for item in before}
        new = {item["id"]: item for item in after}
        changes.extend(document_changes(old, new, path))
        if list(old) != list(new):
            changes.append(
                {"path": path + ["$order"], "before": list(old), "after": list(new)}
            )
    elif before != after:
        changes.append({"path": path, "before": before, "after": after})
    return changes


def ensure_project(
    project_id: str, project_name: str, synopsis: str | None, team_id: str | None = None
) -> dict[str, Any]:
    if project_id:

        def change(project: dict[str, Any]) -> None:
            if project_name.strip():
                project["name"] = project_name.strip()
            if synopsis is not None:
                project["synopsis"] = synopsis.strip()

        return mutate_project(project_id, change)

    with Session(get_engine()) as session, session.begin():
        actor = current_actor.get()
        if team_id:
            from .security import require_team

            require_team(session, team_id)
        document = normalize_project(
            {
                "id": uuid.uuid4().hex,
                "name": project_name.strip() or f"Project {uuid.uuid4().hex[:8]}",
                "synopsis": (synopsis or "").strip(),
            }
        )
        session.add(
            Project(
                id=document["id"],
                document=document,
                owner_id=actor.id if actor else None,
                team_id=team_id,
            )
        )
        session.add(
            ProjectEvent(
                project_id=document["id"],
                operation="project.create",
                actor_id=actor.id if actor else None,
                changes=[{"path": [], "before": None, "after": deepcopy(document)}],
            )
        )
        return {**document, "owner_id": actor.id if actor else None, "team_id": team_id}


def list_project_events(
    project_id: str, limit: int = 50, before: int | None = None
) -> list[dict[str, Any]]:
    get_project(project_id)
    with Session(get_engine()) as session:
        query = select(ProjectEvent).where(ProjectEvent.project_id == project_id)
        if before is not None:
            query = query.where(ProjectEvent.id < before)
        rows = session.scalars(query.order_by(ProjectEvent.id.desc()).limit(limit))
        return [
            {
                "id": row.id,
                "operation": row.operation,
                "actor_id": row.actor_id,
                "changes": row.changes,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
