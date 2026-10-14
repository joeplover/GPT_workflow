"""Explicit, scoped artifact destruction with durable and retryable tombstones."""

from copy import deepcopy
from datetime import datetime, timezone
import logging
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import cast, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import Session

from .. import database
from ..models import AssetDeletion, ImageGeneration, Project, ProjectEvent
from ..repository import document_changes, now_iso, project_document
from ..security import require_actor, require_project_access
from . import images

logger = logging.getLogger(__name__)
IMAGE_FIELDS = {"image_url", "source_image", "original_source_image", "mask_image"}


def lock_assets(session, *, exclusive=False):
    # Imports, generation leases and project mutations take the same short lock.
    # Normal project writes share it and can still run concurrently.
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    session.execute(text(f"SELECT {function}(719284502)"))


def image_urls(value):
    """Only structured image fields count; prompt text can never grant access."""
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in IMAGE_FIELDS and isinstance(item, str):
                result.add(item)
            elif key == "reference_images" and isinstance(item, list):
                result.update(url for url in item if isinstance(url, str))
            elif isinstance(item, (dict, list)):
                result.update(image_urls(item))
    elif isinstance(value, list):
        for item in value:
            result.update(image_urls(item))
    return result


def references(column, url):
    variables = literal({"url": url}, type_=JSONB)
    paths = [f"$.**.{key} ? (@ == $url)" for key in IMAGE_FIELDS]
    paths.append("$.**.reference_images[*] ? (@ == $url)")
    return or_(
        *(
            func.jsonb_path_exists(column, cast(path, JSONPATH), variables)
            for path in paths
        )
    )


def event_urls(changes):
    result = image_urls(changes)
    for change in changes:
        path = change.get("path", [])
        if path and path[-1] in IMAGE_FIELDS:
            result.update(
                v
                for v in (change.get("before"), change.get("after"))
                if isinstance(v, str)
            )
        elif path and path[-1] == "reference_images":
            for key in ("before", "after"):
                result.update(v for v in (change.get(key) or []) if isinstance(v, str))
    return result


def assert_available(session, value):
    urls = image_urls(value)
    if urls and session.scalar(
        select(AssetDeletion.image_url)
        .where(AssetDeletion.image_url.in_(urls))
        .limit(1)
    ):
        raise HTTPException(409, "引用的图片已被彻底删除，请刷新页面并重新选择图片。")


def clear_url(value, url):
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key in IMAGE_FIELDS and item == url:
                value[key] = None
            elif key == "reference_images" and isinstance(item, list):
                value[key] = [v for v in item if v != url]
            elif key in {"shots", "versions"} and isinstance(item, list):
                value[key] = [v for v in item if v.get("image_url") != url]
                clear_url(value[key], url)
            elif isinstance(item, (dict, list)):
                clear_url(item, url)
    elif isinstance(value, list):
        for item in value:
            clear_url(item, url)


def asset_path(url):
    parts = PurePosixPath(url).parts
    if (
        len(parts) != 3
        or parts[0] != "/"
        or parts[1] not in {"uploads", "outputs"}
        or parts[2] in {".", ".."}
        or any(c in url for c in ("?", "#", "\\", "%", "\x00"))
        or url != f"/{parts[1]}/{parts[2]}"
    ):
        raise HTTPException(400, "只能彻底删除本系统保存的图片文件。")
    directory = images.UPLOAD_DIR if parts[1] == "uploads" else images.OUTPUT_DIR
    path = directory / parts[2]
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise HTTPException(400, "无效的图片文件路径。")
    return path


def finish_deletion(url):
    # The tombstone commits first; failed unlinks remain pending for retry/startup.
    path = asset_path(url)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "Physical image removal failed for %s: %s", url, type(exc).__name__
        )
        raise HTTPException(
            503, "图片引用已移除，但文件清理失败。请重试彻底删除，或重启服务重试清理。"
        ) from exc
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(AssetDeletion, url, with_for_update=True)
        row.file_removed = True


def retry_pending_deletions():
    with Session(database.get_engine()) as session:
        urls = list(
            session.scalars(
                select(AssetDeletion.image_url).where(
                    AssetDeletion.file_removed.is_(False)
                )
            )
        )
    for url in urls:
        try:
            finish_deletion(url)
        except HTTPException:
            logger.warning("Image cleanup remains pending: %s", url)


def purge_image(project_id, url):
    asset_path(url)
    actor = require_actor()
    with Session(database.get_engine()) as session, session.begin():
        lock_assets(session, exclusive=True)
        project = session.get(Project, project_id, with_for_update=True)
        if not project:
            raise HTTPException(404, "项目不存在或无权访问。")
        require_project_access(session, project)
        tombstone = session.get(AssetDeletion, url)
        if tombstone:
            if tombstone.project_id != project_id:
                raise HTTPException(404, "图片不属于此项目。")
        else:
            records = list(
                session.scalars(
                    select(ImageGeneration).where(
                        or_(
                            ImageGeneration.image_url == url,
                            references(ImageGeneration.request, url),
                        )
                    )
                )
            )
            belongs = url in image_urls(project.document) or any(
                r.project_id == project_id
                and (r.image_url == url or url in image_urls(r.request))
                for r in records
            )
            if not belongs:
                belongs = any(
                    url in event_urls(changes)
                    for changes in session.scalars(
                        select(ProjectEvent.changes).where(
                            ProjectEvent.project_id == project_id
                        )
                    )
                )
            if not belongs:
                raise HTTPException(404, "图片不属于此项目或无权访问。")
            if session.scalar(
                select(Project.id)
                .where(Project.id != project_id, references(Project.document, url))
                .limit(1)
            ) or any(
                r.project_id != project_id
                and (r.image_url == url or url in image_urls(r.request))
                for r in records
            ):
                raise HTTPException(
                    409, "图片仍被其他项目或其生成记录引用，不能彻底删除。"
                )
            now = datetime.now(timezone.utc)
            if any(
                r.status == "generating"
                and (r.expires_at is None or r.expires_at > now)
                and (r.image_url == url or url in image_urls(r.request))
                for r in records
            ):
                raise HTTPException(409, "图片正在用于生成，请等待任务结束后再删除。")
            from ..routers.frames import require_idle

            card_ids = {
                kind: {
                    c["id"]
                    for c in project.document.get(f"{kind}_cards", [])
                    if c.get("image_url") == url
                }
                for kind in ("character", "scene")
            }
            for frame in project.document.get("frames", []):
                if url in image_urls(frame) or any(
                    card_ids[kind].intersection(frame.get(f"{kind}_card_ids", []))
                    for kind in card_ids
                ):
                    require_idle(frame)
            before = deepcopy(project.document)
            document = deepcopy(before)
            clear_url(document, url)
            for frame in document.get("frames", []):
                if any(
                    f.get("id") == frame.get("id") and f.get("image_url") == url
                    for f in before.get("frames", [])
                ):
                    frame.update(status="draft", error="", updated_at=now_iso())
            for index, shot in enumerate(document.get("shots", []), 1):
                shot["shot_number"] = index
            document["updated_at"] = now_iso()
            project.document = document
            project.updated_at = func.now()
            session.add(
                ProjectEvent(
                    project_id=project_id,
                    actor_id=actor.id,
                    operation="image.purge",
                    changes=document_changes(before, document)
                    + [{"path": ["purged_image"], "before": url, "after": None}],
                )
            )
            session.add(
                AssetDeletion(image_url=url, project_id=project_id, actor_id=actor.id)
            )
        result = project_document(project)
    finish_deletion(url)
    return {"image_url": url, "file_removed": True, "project": result}
