"""Integration tests isolate their data in a temporary PostgreSQL schema."""

from functools import lru_cache
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from web import app as application
from web import config, database, repository
from web.routers import system
from web.services import images
from web.security import actor_from_user, current_actor
from web.models import User
from sqlalchemy.orm import Session


@pytest.fixture
def environment(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    schema = "test_workflow_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True
    )

    @lru_cache(maxsize=1)
    def test_engine():
        return engine

    for module in (database, repository, system):
        monkeypatch.setattr(module, "get_engine", test_engine)
    for module in (config, application, images, system, database):
        for key, value in {
            "RUNTIME_DIR": tmp_path,
            "UPLOAD_DIR": tmp_path / "uploads",
            "OUTPUT_DIR": tmp_path / "outputs",
            "PROJECTS_FILE": tmp_path / "projects.json",
        }.items():
            if hasattr(module, key):
                monkeypatch.setattr(module, key, value)
    monkeypatch.setenv("HICODE_API_KEY", "server-test-secret")
    monkeypatch.setenv("HICODE_MAX_PROJECT_SHOTS", "200")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    try:
        yield tmp_path, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def client(environment):
    with TestClient(application.create_app()) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "test_owner", "password": "test-password-123"},
        )
        assert response.status_code == 201, response.text
        with Session(environment[1]) as session:
            actor = actor_from_user(session.get(User, response.json()["id"]))
        token = current_actor.set(actor)
        try:
            yield client
        finally:
            current_actor.reset(token)
