"""Connection lifecycle, initial schema and one-time legacy JSON import."""

import json
import logging
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import PROJECTS_FILE, database_url
from .models import Base, Migration, Project

logger = logging.getLogger(__name__)
LEGACY_MIGRATION = "projects_json_v1"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )


def initialize_database() -> None:
    # Serialize schema initialization and imports across Uvicorn workers/replicas.
    with get_engine().begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(719284501)"))
        Base.metadata.create_all(connection)
        connection.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true"
            )
        )
        # Additive upgrade for installations created before authentication.
        connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_id VARCHAR REFERENCES users(id)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS team_id VARCHAR REFERENCES teams(id)"
            )
        )
        connection.execute(
            text("ALTER TABLE project_events ADD COLUMN IF NOT EXISTS actor_id VARCHAR")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects(owner_id)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_projects_team_id ON projects(team_id)")
        )
        with Session(bind=connection) as session:
            if session.get(Migration, LEGACY_MIGRATION) or not PROJECTS_FILE.exists():
                return
            from .repository import normalize_project

            items = json.loads(PROJECTS_FILE.read_text(encoding="utf-8-sig"))
            if not isinstance(items, list):
                raise ValueError(
                    "Legacy projects.json must contain an array; original file was preserved."
                )
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                ):
                    raise ValueError(
                        "Legacy project has an invalid id; original file was preserved."
                    )
                document = normalize_project(item)
                # Never replace a project already present in the database.
                session.execute(
                    insert(Project)
                    .values(id=item["id"], document=document)
                    .on_conflict_do_nothing()
                )
            session.add(Migration(key=LEGACY_MIGRATION))
            session.flush()
            logger.info(
                "Imported %d legacy project records; source file preserved", len(items)
            )


def dispose_database() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()
