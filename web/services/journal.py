"""Durable generation journal. Never store runtime credentials or delete artifacts."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import database
from ..errors import describe_error, redact
from ..models import ImageGeneration, Migration, Project, ProjectEvent
from ..security import current_actor, require_project_access


def start_generation(
    project,
    *,
    mode,
    prompt,
    system_prompt,
    composed_prompt,
    model,
    size,
    character_card_ids=(),
    scene_card_ids=(),
    reference_images=(),
    source_image=None,
    original_source_image=None,
    mask_image=None,
    frame_id=None,
    timeout=300,
):
    actor = current_actor.get()
    snapshot = {
        "project_name": project.get("name", ""),
        "synopsis": project.get("synopsis", ""),
        "mode": mode,
        "prompt": prompt,
        "system_prompt": system_prompt,
        "composed_prompt": composed_prompt,
        "model": model,
        "size": size,
        "character_card_ids": list(character_card_ids),
        "scene_card_ids": list(scene_card_ids),
        "reference_images": list(reference_images),
        "source_image": source_image,
        "original_source_image": original_source_image,
        "mask_image": mask_image,
        "character_cards": [
            deepcopy(c)
            for c in project["character_cards"]
            if c["id"] in character_card_ids
        ],
        "scene_cards": [
            deepcopy(c) for c in project["scene_cards"] if c["id"] in scene_card_ids
        ],
        "frame_title": next(
            (f.get("title", "") for f in project["frames"] if f["id"] == frame_id), ""
        ),
        "actor_name": actor.display_name if actor else "",
    }
    with Session(database.get_engine()) as session, session.begin():
        from .assets import assert_available, lock_assets

        lock_assets(session)
        row = session.get(Project, project["id"])
        if not row:
            raise HTTPException(404, "项目不存在。")
        require_project_access(session, row)
        assert_available(session, snapshot)
        record = ImageGeneration(
            generation_key=uuid.uuid4().hex,
            project_id=project["id"],
            frame_id=frame_id,
            actor_id=actor.id if actor else None,
            status="generating",
            request=snapshot,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=max(timeout * 6 + 120, 600)),
        )
        session.add(record)
        session.flush()
        return record.id


def update_source(record_id, source_image):
    """Attach the actual collage passed to the relay before issuing the call."""
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(ImageGeneration, record_id, with_for_update=True)
        if row.status == "generating":
            row.request = {**row.request, "source_image": source_image}


def complete_generation(record_id, image_url):
    # Commit the artifact before updating the replaceable workbench pointer.
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(ImageGeneration, record_id, with_for_update=True)
        row.status = "succeeded"
        row.image_url = image_url
        row.finished_at = datetime.now(timezone.utc)
        row.error = ""


def safe_error(exc, secret=""):
    value = (
        str(exc.detail)
        if isinstance(exc, HTTPException)
        else f"{type(exc).__name__}: {exc}"
    )
    return redact(value, secret)


def fail_generation(record_id, exc, secret=""):
    if record_id is None:
        return
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(ImageGeneration, record_id, with_for_update=True)
        if row.status in {"generating", "interrupted"}:
            row.status = "failed"
            row.error = describe_error(exc).message
            row.finished_at = datetime.now(timezone.utc)


def backfill_history(session):
    """Recover identifiable generated outputs from previews and immutable audit events."""
    key = "image_journal_backfill_v1"
    if session.get(Migration, key):
        return
    fields = {
        "prompt",
        "system_prompt",
        "composed_prompt",
        "model",
        "size",
        "mode",
        "source_image",
        "mask_image",
        "reference_images",
        "reference_sources",
        "character_card_ids",
        "scene_card_ids",
    }
    for project in session.scalars(select(Project)).yield_per(100):
        recovered = {}

        def collect(value, frame_id=None, timestamp=None, actor_id=None):
            if isinstance(value, list):
                for item in value:
                    collect(item, frame_id, timestamp, actor_id)
            elif isinstance(value, dict):
                if "versions" in value:
                    frame_id = value.get("id", frame_id)
                    collect(value["versions"], frame_id, timestamp, actor_id)
                url = value.get("image_url")
                if isinstance(url, str) and url.startswith("/outputs/"):
                    metadata = {k: deepcopy(v) for k, v in value.items() if k in fields}
                    # Frame drafts may have changed since the image was generated.
                    if "versions" in value:
                        metadata = {
                            k: value.get("generated_" + k, "")
                            for k in ("prompt", "system_prompt")
                        }
                    item = recovered.setdefault(
                        url,
                        {
                            "request": {},
                            "frame_id": frame_id,
                            "created_at": value.get("created_at") or timestamp,
                            "actor_id": actor_id,
                        },
                    )
                    for name, data in metadata.items():
                        if data and not item["request"].get(name):
                            item["request"][name] = data
                    if frame_id:
                        item["frame_id"] = frame_id
                    if actor_id and not item["actor_id"]:
                        item["actor_id"] = actor_id
                # Restrict traversal to server-owned history containers.
                for name in ("shots", "frames", "before", "after"):
                    if name in value:
                        collect(value[name], frame_id, timestamp, actor_id)

        collect(project.document)
        for event in session.scalars(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project.id)
            .order_by(ProjectEvent.id)
        ).yield_per(200):
            for change in event.changes:
                path = change.get("path", [])
                if not path or path[0] not in {"shots", "frames"}:
                    continue
                frame_id = (
                    path[1]
                    if path[0] == "frames" and len(path) > 1 and path[1] != "$order"
                    else None
                )
                for side in ("before", "after"):
                    value = change.get(side)
                    if path[-1] == "image_url" and isinstance(value, str):
                        value = {"image_url": value}
                    collect(
                        value,
                        frame_id,
                        event.created_at.isoformat(),
                        event.actor_id
                        if event.operation
                        in {"frame.generate.complete", "image.generate.complete"}
                        and side == "after"
                        else None,
                    )
        for url, item in recovered.items():
            if session.scalar(
                select(ImageGeneration.id)
                .where(
                    ImageGeneration.project_id == project.id,
                    ImageGeneration.image_url == url,
                )
                .limit(1)
            ):
                continue
            stamp = item["created_at"]
            try:
                created = datetime.fromisoformat(stamp) if stamp else project.created_at
            except (ValueError, TypeError):
                created = project.created_at
            session.add(
                ImageGeneration(
                    generation_key="legacy:"
                    + hashlib.sha256(f"{project.id}:{url}".encode()).hexdigest(),
                    project_id=project.id,
                    frame_id=item["frame_id"],
                    actor_id=item["actor_id"],
                    request={
                        "project_name": project.document.get("name", ""),
                        **item["request"],
                    },
                    image_url=url,
                    status="succeeded",
                    historical=True,
                    error="",
                    created_at=created,
                    finished_at=created,
                )
            )
    session.add(Migration(key=key))
    session.flush()
