"""Admin-only exception review; never grants access to private project content."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from .. import database
from ..models import ExceptionRecord
from ..security import require_actor

router = APIRouter(prefix="/api/admin/exceptions", tags=["exceptions"])


def require_admin():
    actor = require_actor()
    if not actor.is_admin:
        raise HTTPException(403, "只有系统管理员可以管理异常。")
    return actor


def serialize(row, detail=False):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "code",
            "message",
            "status_code",
            "method",
            "path",
            "exception_type",
            "resolved",
        )
    }
    result["created_at"] = row.created_at.isoformat()
    if detail:
        result.update(
            diagnostic=row.diagnostic,
            actor_id=row.actor_id,
            resolved_by=row.resolved_by,
            resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
        )
    return result


@router.get("")
def list_exceptions(
    response: Response,
    resolved: bool | None = None,
    code: str = Query("", max_length=80),
    request_id: str = Query("", max_length=100),
    before: str = Query("", max_length=150),
    limit: int = Query(30, ge=1, le=100),
):
    require_admin()
    response.headers["Cache-Control"] = "private, no-store"
    filters = []
    if resolved is not None:
        filters.append(ExceptionRecord.resolved == resolved)
    if code:
        filters.append(ExceptionRecord.code == code)
    if request_id:
        filters.append(ExceptionRecord.id == request_id)
    if before:
        try:
            timestamp, record_id = before.rsplit("|", 1)
            moment = datetime.fromisoformat(timestamp)
            if moment.tzinfo is None or not record_id:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "分页参数无效，请刷新记录。") from None
        filters.append(
            tuple_(ExceptionRecord.created_at, ExceptionRecord.id) < (moment, record_id)
        )
    with Session(database.get_engine()) as session:
        rows = list(
            session.scalars(
                select(ExceptionRecord)
                .where(*filters)
                .order_by(ExceptionRecord.created_at.desc(), ExceptionRecord.id.desc())
                .limit(limit + 1)
            )
        )
        more = len(rows) > limit
        last = rows[limit - 1] if more else None
        return {
            "items": [serialize(row) for row in rows[:limit]],
            "has_more": more,
            "next_before": f"{last.created_at.isoformat()}|{last.id}" if last else None,
            "unresolved": session.scalar(
                select(func.count())
                .select_from(ExceptionRecord)
                .where(ExceptionRecord.resolved.is_(False))
            ),
        }


@router.get("/{record_id}")
def get_exception(record_id: str, response: Response):
    require_admin()
    response.headers["Cache-Control"] = "private, no-store"
    with Session(database.get_engine()) as session:
        row = session.get(ExceptionRecord, record_id)
        if not row:
            raise HTTPException(404, "异常记录不存在。")
        return serialize(row, detail=True)


class ResolutionInput(BaseModel):
    resolved: bool


@router.post("/{record_id}/resolution")
def resolve_exception(record_id: str, body: ResolutionInput):
    actor = require_admin()
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(ExceptionRecord, record_id, with_for_update=True)
        if not row:
            raise HTTPException(404, "异常记录不存在。")
        row.resolved = body.resolved
        row.resolved_by = actor.id if body.resolved else None
        row.resolved_at = datetime.now(timezone.utc) if body.resolved else None
        return serialize(row, detail=True)
