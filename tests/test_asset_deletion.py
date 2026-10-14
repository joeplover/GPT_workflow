from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from web import database
from web.app import create_app
from web.models import AssetDeletion, ProjectEvent
from web.repository import mutate_project
from web.routers import frames
from web.services import assets, images
from web.services.journal import start_generation
from test_images import png_bytes
from test_journal import project_and_frame, relay_factory


def purge(client, project_id, url):
    return client.request(
        "DELETE",
        f"/photodream/api/projects/{project_id}/assets",
        json={"image_url": url},
    )


def card_with_image(client, base):
    return client.post(
        f"{base}/cards",
        data={"card_type": "scene", "name": "海边", "content": "日落"},
        files={"image": ("scene.png", png_bytes(), "image/png")},
    ).json()["scene_cards"][0]


def test_purge_generated_image_cleans_previews_preserves_archive(client, monkeypatch):
    project, base, frame = project_and_frame(client)
    monkeypatch.setattr(frames, "relay_generate_raw", relay_factory([]))
    result = client.post(
        f"{base}/frames/{frame['id']}/generate",
        json={"prompt": "测试海边", "system_prompt": "电影感"},
    )
    assert result.status_code == 200, result.text
    url = result.json()["image_url"]
    path = images.OUTPUT_DIR / Path(url).name
    assert path.exists()
    record = client.get("/photodream/api/generations").json()["items"][0]
    reply = purge(client, project["id"], url)
    assert reply.status_code == 200, reply.text
    assert reply.json()["file_removed"] and not path.exists()
    frame = client.get(base).json()["frames"][0]
    assert (
        frame["image_url"] is None
        and frame["status"] == "draft"
        and frame["versions"] == []
    )
    assert client.get("/photodream" + url).status_code == 404
    archived = client.get(f"/photodream/api/generations/{record['id']}").json()
    assert archived["request"] == record["request"]
    assert archived["image_url"] == url and archived["image_deleted"]
    assert archived["deleted_images"][url]["file_removed"]
    with Session(database.get_engine()) as session:
        assert session.scalar(
            select(ProjectEvent.id).where(ProjectEvent.operation == "image.purge")
        )
    assert purge(client, project["id"], url).status_code == 200


def test_purge_uploaded_card_keeps_text_and_selections(client):
    project, base, frame = project_and_frame(client)
    card = card_with_image(client, base)
    client.post(
        f"{base}/frames/{frame['id']}",
        json={"prompt": "海边", "scene_card_ids": [card["id"]]},
    )
    path = images.UPLOAD_DIR / Path(card["image_url"]).name
    response = purge(client, project["id"], card["image_url"])
    assert response.status_code == 200, response.text
    updated = client.get(base).json()
    assert not path.exists()
    assert updated["scene_cards"][0]["image_url"] is None
    assert updated["scene_cards"][0]["content"] == "日落"
    assert updated["frames"][0]["scene_card_ids"] == [card["id"]]


def test_purge_reference_keeps_snapshot_and_marks_only_reference_deleted(
    client, monkeypatch
):
    project, base, frame = project_and_frame(client)
    card = card_with_image(client, base)
    monkeypatch.setattr(frames, "relay_edit_raw", relay_factory([]))
    response = client.post(
        f"{base}/frames/{frame['id']}/generate",
        json={"prompt": "日落", "scene_card_ids": [card["id"]]},
    )
    assert response.status_code == 200, response.text
    original = client.get("/photodream/api/generations").json()["items"][0]
    assert purge(client, project["id"], card["image_url"]).status_code == 200
    archived = client.get(f"/photodream/api/generations/{original['id']}").json()
    assert archived["request"] == original["request"]
    assert archived["deleted_images"][card["image_url"]]["file_removed"]
    assert not archived["image_deleted"]
    assert client.get("/photodream" + archived["image_url"]).status_code == 200


def test_active_single_generation_protects_reference_file(client, monkeypatch):
    from web.routers import images as routes

    project, base, _ = project_and_frame(client)
    card = card_with_image(client, base)
    relay = relay_factory([])

    def protected_relay(**kwargs):
        reply = purge(client, project["id"], card["image_url"])
        assert reply.status_code == 409, reply.text
        return relay(**kwargs)

    monkeypatch.setattr(routes, "relay_edit_raw", protected_relay)
    response = client.post(
        "/photodream/api/generate",
        data={
            "project_id": project["id"],
            "prompt": "海边",
            "scene_card_ids": card["id"],
        },
    )
    assert response.status_code == 200, response.text
    assert purge(client, project["id"], card["image_url"]).status_code == 200


def test_purge_authorization_project_match_and_prompt_does_not_grant_access(client):
    project, base, _ = project_and_frame(client)
    card = card_with_image(client, base)
    other = client.post(
        "/photodream/api/projects",
        data={"name": "其他项目", "synopsis": card["image_url"]},
    ).json()
    assert purge(client, other["id"], card["image_url"]).status_code == 404
    with TestClient(create_app()) as stranger:
        stranger.post(
            "/photodream/api/auth/register",
            json={"username": "stranger", "password": "test-password-123"},
        )
        assert purge(stranger, project["id"], card["image_url"]).status_code == 404
    assert (images.UPLOAD_DIR / Path(card["image_url"]).name).exists()


def test_shared_resource_prevents_destruction(client):
    project, base, _ = project_and_frame(client)
    card = card_with_image(client, base)
    other = client.post("/photodream/api/projects", data={"name": "引用项目"}).json()
    response = client.post(
        f"/photodream/api/projects/{other['id']}/resources/import",
        json={
            "source_project_id": project["id"],
            "card_type": "scene",
            "card_ids": [card["id"]],
        },
    )
    assert response.status_code == 200, response.text
    assert purge(client, project["id"], card["image_url"]).status_code == 409
    assert (images.UPLOAD_DIR / Path(card["image_url"]).name).exists()


def test_busy_reference_and_stale_draft_cannot_restore_deleted_file(client):
    project, base, frame = project_and_frame(client)
    card = card_with_image(client, base)
    stale = client.get(base).json()
    client.post(
        f"{base}/frames/{frame['id']}",
        json={"prompt": "海边", "scene_card_ids": [card["id"]]},
    )
    mutate_project(
        project["id"],
        lambda p: p["frames"][0].update(
            status="generating",
            lease_until=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        ),
    )
    assert purge(client, project["id"], card["image_url"]).status_code == 409
    mutate_project(project["id"], lambda p: p["frames"][0].update(status="draft"))
    assert purge(client, project["id"], card["image_url"]).status_code == 200
    with pytest.raises(HTTPException) as failure:
        start_generation(
            stale,
            mode="generate",
            prompt="海边",
            system_prompt="",
            composed_prompt="海边",
            model="test",
            size="1024x1024",
            scene_card_ids=[card["id"]],
        )
    assert failure.value.status_code == 409
    with pytest.raises(HTTPException) as failure:
        mutate_project(
            project["id"],
            lambda p: p["scene_cards"][0].update(image_url=card["image_url"]),
        )
    assert failure.value.status_code == 409


def test_purge_recovers_images_from_removed_card_audit(client):
    project, base, _ = project_and_frame(client)
    card = card_with_image(client, base)
    assert client.delete(f"{base}/cards/scene/{card['id']}").status_code == 200
    assert purge(client, project["id"], card["image_url"]).status_code == 200


def test_invalid_paths_and_symlinks_never_delete_external_file(client, tmp_path):
    project, base, _ = project_and_frame(client)
    external = tmp_path / "do-not-delete.png"
    external.write_bytes(png_bytes())
    (images.UPLOAD_DIR / "link.png").symlink_to(external)
    for url in [
        "/uploads/../do-not-delete.png",
        "/outputs/a?b",
        "https://example.org/img.png",
        "/uploads/link.png",
        "/uploads//file.png",
        "/uploads/a\x00.png",
    ]:
        assert purge(client, project["id"], url).status_code == 400
    assert external.exists()


def test_unlink_failure_is_pending_and_retryable(client, monkeypatch):
    project, base, _ = project_and_frame(client)
    card = card_with_image(client, base)
    path = images.UPLOAD_DIR / Path(card["image_url"]).name
    unlink = Path.unlink

    def fail(target, *args, **kwargs):
        if target == path:
            raise PermissionError("test-only denial")
        return unlink(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert purge(client, project["id"], card["image_url"]).status_code == 503
    assert (
        path.exists()
        and client.get("/photodream" + card["image_url"]).status_code == 404
    )
    with Session(database.get_engine()) as session:
        assert not session.get(AssetDeletion, card["image_url"]).file_removed
    assets.retry_pending_deletions()
    assert not path.exists()
    assert purge(client, project["id"], card["image_url"]).status_code == 200
