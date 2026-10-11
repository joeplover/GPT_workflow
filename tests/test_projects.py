from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from web.app import create_app
from web.database import LEGACY_MIGRATION, initialize_database
from web.models import Migration, Project
from web.repository import ensure_project, get_project, mutate_project
from web.services.storyboard import add_shot_to_project


def test_project_cards_screenplay_and_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json() == {
        "status": "ok",
        "database": "postgresql",
    }
    assert "server-test-secret" not in client.get("/api/config").text
    assert client.get("/api/config").json()["has_api_key"] is True
    project = client.post(
        "/api/projects", data={"name": "测试", "synopsis": "剧情"}
    ).json()
    project_id = project["id"]
    response = client.post(
        f"/api/projects/{project_id}/cards",
        data={
            "card_type": "character",
            "name": "人物",
            "content": "蓝色衣服",
        },
    )
    assert response.status_code == 200
    assert response.json()["character_cards"][0]["name"] == "人物"
    response = client.post(
        f"/api/projects/{project_id}/screenplay",
        data={"screenplay_text": "1. 开门\n2. 出门"},
    )
    assert len(response.json()["shot_plan"]) == 2
    assert len(client.get("/api/projects").json()) == 1
    assert client.get("/api/projects/missing").status_code == 404
    assert (
        client.post(
            f"/api/projects/{project_id}/cards",
            data={
                "card_type": "invalid",
                "name": "人物",
                "content": "蓝色衣服",
            },
        ).status_code
        == 400
    )


def test_legacy_import_is_idempotent_and_preserves_database(environment):
    path, engine = environment
    initialize_database()
    existing = ensure_project("", "数据库里的名称", "")
    source = path / "projects.json"
    original = json.dumps(
        [
            {"id": existing["id"], "name": "旧名称"},
            {
                "id": "legacy",
                "name": "旧项目",
                "shots": [{"id": "old-shot", "image_url": "/outputs/old.png"}],
            },
        ]
    )
    source.write_text(original, encoding="utf-8")
    initialize_database()
    initialize_database()
    assert get_project(existing["id"])["name"] == "数据库里的名称"
    assert get_project("legacy")["shots"][0]["image_url"] == "/outputs/old.png"
    assert source.read_text(encoding="utf-8") == original
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 2
        assert session.get(Migration, LEGACY_MIGRATION) is not None
    # Persistence survives application shutdown/restart.
    with TestClient(create_app()) as restarted:
        # Legacy projects remain private until explicitly assigned by local admin.
        assert restarted.get("/api/projects/legacy").status_code == 401


def test_invalid_legacy_file_rolls_back_entire_import(environment):
    path, engine = environment
    initialize_database()
    source = path / "projects.json"
    source.write_text(
        '[{"id":"valid","name":"valid"}, {"name":"missing id"}]', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid id"):
        initialize_database()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0
        assert session.get(Migration, LEGACY_MIGRATION) is None
    source.write_text('[{"id":"valid","name":"fixed"}]', encoding="utf-8")
    initialize_database()
    assert get_project("valid")["name"] == "fixed"


def test_failed_mutation_does_not_commit(client):
    project = ensure_project("", "原名称", "")

    def change(document):
        document["name"] = "不该提交"
        raise ValueError("rollback")

    with pytest.raises(ValueError, match="rollback"):
        mutate_project(project["id"], change)
    assert get_project(project["id"])["name"] == "原名称"


def test_concurrent_cards_and_shots_are_not_lost(client, monkeypatch):
    monkeypatch.setenv("HICODE_MAX_PROJECT_SHOTS", "10")
    project = ensure_project("", "并发测试", "")

    def append(index):
        if index % 2:
            response = client.post(
                f"/api/projects/{project['id']}/cards",
                data={
                    "card_type": "scene",
                    "name": f"场景{index}",
                    "content": "场景设定",
                },
            )
            assert response.status_code == 200
        else:
            add_shot_to_project(
                project_id=project["id"],
                mode="generate",
                prompt=str(index),
                composed_prompt=str(index),
                model="test",
                size="1024x1024",
                image_url=f"/outputs/{index}.png",
                source_image=None,
                mask_image=None,
                continue_from_last=False,
                character_card_ids=[],
                scene_card_ids=[],
                reference_images=[],
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))
    saved = get_project(project["id"])
    assert len(saved["scene_cards"]) == 20
    assert len({card["name"] for card in saved["scene_cards"]}) == 20
    assert len(saved["shots"]) == 10
    assert len({shot["id"] for shot in saved["shots"]}) == 10
    assert [shot["shot_number"] for shot in saved["shots"]] == list(range(1, 11))
