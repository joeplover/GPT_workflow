from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from web import database
from web.models import ImageGeneration, Migration
from web.repository import mutate_project
from web.routers import frames, images
from web.services import images as files
from test_images import png_bytes


def project_and_frame(client):
    project = client.post(
        "/photodream/api/projects", data={"name": "可追溯项目"}
    ).json()
    url = f"/photodream/api/projects/{project['id']}"
    frame = client.post(f"{url}/frames/initialize").json()[0]
    return project, url, frame


def relay_factory(calls):
    def relay(**kwargs):
        calls.append(kwargs)
        path = files.OUTPUT_DIR / f"archive_{len(calls)}.png"
        path.write_bytes(png_bytes())
        # The request is durable before the upstream call.
        with Session(database.get_engine()) as session:
            assert session.scalar(
                select(ImageGeneration.id).where(ImageGeneration.status == "generating")
            )
        return {"image_url": f"/outputs/{path.name}"}

    return relay


def test_archive_outlives_version_cap_card_and_frame_deletion(client, monkeypatch):
    project, url, frame = project_and_frame(client)
    card = client.post(
        f"{url}/cards",
        data={"card_type": "character", "name": "主角", "content": "蓝衣"},
        files={"image": ("hero.png", png_bytes(), "image/png")},
    ).json()["character_cards"][0]
    calls = []
    monkeypatch.setattr(frames, "relay_edit_raw", relay_factory(calls))
    for index in range(23):
        result = client.post(
            f"{url}/frames/{frame['id']}/generate",
            json={
                "prompt": f"海边 {index}",
                "system_prompt": "电影感",
                "model": "custom-model",
                "size": "1920 × 1080",
                "character_card_ids": [card["id"]],
                "api_key": "transient-key",
            },
        )
        assert result.status_code == 200, result.text
    assert len(result.json()["versions"]) == 20
    assert client.delete(f"{url}/cards/character/{card['id']}").status_code == 200
    assert client.delete(f"{url}/frames/{frame['id']}").status_code == 200
    page = client.get(
        f"/photodream/api/generations?project_id={project['id']}&frame_id={frame['id']}&limit=10"
    ).json()
    assert len(page["items"]) == 10
    more = client.get(
        f"/photodream/api/generations?before={page['next_before']}&limit=100"
    ).json()["items"]
    records = page["items"] + more
    assert len(records) == 23
    oldest = records[-1]
    q = oldest["request"]
    assert q["prompt"] == "海边 0"
    assert q["composed_prompt"] == calls[0]["prompt"]
    assert q["character_cards"] == [card]
    assert q["size"] == "1920x1080" and q["model"] == "custom-model"
    assert "transient-key" not in str(records)
    assert client.get(f"/photodream/api/generations/{oldest['id']}").json() == oldest
    download = client.get("/photodream" + oldest["image_url"] + "?download=true")
    assert download.content == png_bytes()
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get("/photodream" + card["image_url"]).status_code == 200
    assert client.get("/photodream/api/generations?limit=101").status_code == 422


def test_single_generate_edit_failure_and_history_cap(client, monkeypatch):
    project, url, _ = project_and_frame(client)
    monkeypatch.setenv("HICODE_MAX_PROJECT_SHOTS", "2")
    calls = []
    for module in (frames, images):
        monkeypatch.setattr(module, "relay_generate_raw", relay_factory(calls))
        monkeypatch.setattr(module, "relay_edit_raw", relay_factory(calls))
    for index in range(4):
        assert (
            client.post(
                "/photodream/api/generate",
                data={
                    "project_id": project["id"],
                    "prompt": f"单图 {index}",
                    "system_prompt": "画风",
                },
            ).status_code
            == 200
        )
    assert len(client.get(url).json()["shots"]) == 2
    assert (
        client.post(
            "/photodream/api/edit",
            data={"project_id": project["id"], "prompt": "编辑天空"},
            files={"mask": ("mask.png", png_bytes(), "image/png")},
        ).status_code
        == 200
    )
    secret = "test-private-key"

    def fail(**kwargs):
        raise HTTPException(
            400,
            f"invalid api_key={secret}; https://user:password@relay/error?token=unknown",
        )

    monkeypatch.setattr(images, "relay_generate_raw", fail)
    assert (
        client.post(
            "/photodream/api/generate",
            data={"project_id": project["id"], "prompt": "失败", "api_key": secret},
        ).status_code
        == 400
    )
    page = client.get("/photodream/api/generations").json()["items"]
    assert len(page) == 6 and page[0]["status"] == "failed"
    assert secret not in str(page) and "password@" not in str(page)
    assert page[1]["request"]["mode"] == "edit"
    assert page[1]["request"]["mask_image"].startswith("/uploads/")
    assert page[-1]["image_url"] == "/outputs/archive_1.png"


def test_output_archived_even_when_project_update_fails(client, monkeypatch):
    project, _, _ = project_and_frame(client)
    monkeypatch.setattr(images, "relay_generate_raw", relay_factory([]))

    def cannot_associate(**kwargs):
        raise RuntimeError("project update failed")

    monkeypatch.setattr(images, "add_shot_to_project", cannot_associate)
    with pytest.raises(RuntimeError, match="project update failed"):
        client.post(
            "/photodream/api/generate",
            data={"project_id": project["id"], "prompt": "已生成但关联失败"},
        )
    item = client.get("/photodream/api/generations").json()["items"][0]
    assert item["status"] == "succeeded"
    assert (
        client.get("/photodream" + item["image_url"] + "?download=true").content
        == png_bytes()
    )


def test_edit_collage_preserves_original_source_in_journal(client, monkeypatch):
    from web.models import ProjectEvent

    project, url, _ = project_and_frame(client)
    calls = []
    monkeypatch.setattr(images, "relay_edit_raw", relay_factory(calls))
    response = client.post(
        "/photodream/api/edit",
        data={"project_id": project["id"], "prompt": "保留人物，修改天空"},
        files=[
            ("image", ("original.png", png_bytes(), "image/png")),
            ("reference_images", ("reference.png", png_bytes(), "image/png")),
        ],
    )
    assert response.status_code == 200, response.text
    item = client.get("/photodream/api/generations").json()["items"][0]
    snapshot = item["request"]
    assert snapshot["original_source_image"].startswith("/uploads/")
    assert snapshot["source_image"] != snapshot["original_source_image"]
    assert snapshot["source_image"] == files.path_to_upload_url(calls[0]["image_path"])
    assert len(snapshot["reference_images"]) == 1
    assert snapshot["composed_prompt"] == calls[0]["prompt"]
    assert (
        client.delete(f"{url}/shots/{response.json()['shot']['id']}").status_code == 200
    )
    with Session(database.get_engine()) as session, session.begin():
        session.execute(
            delete(ProjectEvent).where(ProjectEvent.project_id == project["id"])
        )
    # The uploaded input only appears in the immutable journal, not current previews.
    original = client.get(
        "/photodream" + snapshot["original_source_image"] + "?download=true"
    )
    assert original.content == png_bytes()
    assert original.headers["content-disposition"].startswith("attachment;")
    assert client.get("/photodream" + snapshot["source_image"]).status_code == 200


def test_card_removal_cleans_associations_and_keeps_original_file(client):
    project, url, frame = project_and_frame(client)
    card = client.post(
        f"{url}/cards",
        data={"card_type": "scene", "name": "海边", "content": "日落"},
        files={"image": ("scene.png", png_bytes(), "image/png")},
    ).json()["scene_cards"][0]
    client.post(
        f"{url}/frames/{frame['id']}",
        json={"prompt": "海边", "scene_card_ids": [card["id"]]},
    )
    client.post(
        f"{url}/workspace",
        json={
            "library_selection": [card["id"]],
            "single_image_draft": {"scene_card_ids": [card["id"]]},
        },
    )
    cleared = client.delete(f"{url}/cards/scene/{card['id']}/image").json()
    assert cleared["scene_cards"][0]["image_url"] is None
    assert cleared["frames"][0]["scene_card_ids"] == [card["id"]]
    assert (
        client.get("/photodream" + card["image_url"] + "?download=true").content
        == png_bytes()
    )
    removed = client.delete(f"{url}/cards/scene/{card['id']}").json()
    assert removed["scene_cards"] == [] and removed["frames"][0]["scene_card_ids"] == []
    assert removed["workspace"]["library_selection"] == []
    assert removed["workspace"]["single_image_draft"]["scene_card_ids"] == []
    stale = client.post(
        f"{url}/frames/{frame['id']}", json={"scene_card_ids": [card["id"]]}
    )
    assert stale.status_code == 200 and stale.json()["scene_card_ids"] == []
    assert (
        client.post(
            f"{url}/frames/{frame['id']}/generate",
            json={"prompt": "旧关联", "scene_card_ids": [card["id"]]},
        ).status_code
        == 400
    )
    assert client.delete(f"{url}/cards/scene/{card['id']}").status_code == 404
    assert client.delete(f"{url}/cards/invalid/{card['id']}").status_code == 400
    events = client.get(f"{url}/events").json()
    assert {e["operation"] for e in events} >= {"card.delete", "card.image.remove"}


def test_busy_card_cannot_be_deleted(client):
    project, url, frame = project_and_frame(client)
    card = client.post(
        f"{url}/cards",
        data={"card_type": "character", "name": "主角", "content": "设定"},
    ).json()["character_cards"][0]

    def busy(project):
        project["frames"][0].update(
            status="generating",
            lease_until=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            character_card_ids=[card["id"]],
        )

    mutate_project(project["id"], busy)
    assert client.delete(f"{url}/cards/character/{card['id']}").status_code == 409
    assert client.delete(f"{url}/cards/character/{card['id']}/image").status_code == 409
    assert client.get(url).json()["character_cards"] == [card]


def test_historical_backfill_from_deleted_frames_and_trimmed_shots_is_idempotent(
    client,
):
    project, url, frame = project_and_frame(client)
    stamp = datetime.now(timezone.utc).isoformat()

    def legacy(document):
        document["shots"] = [
            {
                "id": "legacy-shot",
                "image_url": "/outputs/legacy-shot.png",
                "prompt": "旧单图",
                "composed_prompt": "最终旧单图",
                "created_at": stamp,
            }
        ]
        document["frames"][0].update(
            image_url="/outputs/legacy-frame.png",
            prompt="已修改的新草稿",
            generated_prompt="旧镜头",
            versions=[
                {
                    "image_url": "/outputs/legacy-frame.png",
                    "prompt": "旧镜头",
                    "composed_prompt": "最终旧镜头",
                    "created_at": stamp,
                }
            ],
        )

    mutate_project(project["id"], legacy, "legacy.generate.complete")
    mutate_project(
        project["id"], lambda document: document.update(shots=[]), "history.trim"
    )
    client.delete(f"{url}/frames/{frame['id']}")
    with Session(database.get_engine()) as session, session.begin():
        session.execute(
            delete(Migration).where(Migration.key == "image_journal_backfill_v1")
        )
    database.initialize_database()
    database.initialize_database()
    records = client.get("/photodream/api/generations").json()["items"]
    assert len(records) == 2 and all(r["historical"] for r in records)
    by_url = {r["image_url"]: r for r in records}
    assert by_url["/outputs/legacy-frame.png"]["request"]["prompt"] == "旧镜头"
    assert (
        by_url["/outputs/legacy-frame.png"]["request"]["composed_prompt"]
        == "最终旧镜头"
    )
    assert by_url["/outputs/legacy-shot.png"]["request"]["prompt"] == "旧单图"


def test_journal_and_download_are_private_and_expired_requests_recover(
    client, monkeypatch
):
    project, url, frame = project_and_frame(client)

    def timeout(**kwargs):
        raise requests.Timeout("relay timed out")

    monkeypatch.setattr(frames, "relay_generate_raw", timeout)
    assert (
        client.post(
            f"{url}/frames/{frame['id']}/generate", json={"prompt": "失败提示词"}
        ).status_code
        == 502
    )
    item = client.get("/photodream/api/generations").json()["items"][0]
    assert item["status"] == "failed" and item["request"]["prompt"] == "失败提示词"
    with Session(database.get_engine()) as session, session.begin():
        row = session.get(ImageGeneration, item["id"])
        row.status = "generating"
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (
        client.get("/photodream/api/generations?status=interrupted").json()["items"][0][
            "id"
        ]
        == item["id"]
    )
    # Log in as a second user; their journal cannot disclose the first user's records.
    assert (
        client.post(
            "/photodream/api/auth/register",
            json={"username": "other_user", "password": "other-password-123"},
        ).status_code
        == 201
    )
    assert client.get("/photodream/api/generations").json()["items"] == []
    assert client.get(f"/photodream/api/generations/{item['id']}").status_code == 404
    assert (
        client.get(
            f"/photodream/api/generations?project_id={project['id']}"
        ).status_code
        == 404
    )
    assert client.delete(f"{url}/cards/character/missing").status_code == 404
    assert (
        client.get("/photodream/outputs/archive_1.png?download=true").status_code == 404
    )


def test_journal_only_artifact_access_obeys_team_membership(environment, monkeypatch):
    from fastapi.testclient import TestClient
    from test_auth_teams import signup, create_project
    from web.app import create_app
    from web.models import ProjectEvent

    monkeypatch.setattr(images, "relay_generate_raw", relay_factory([]))
    with TestClient(create_app()) as alice, TestClient(create_app()) as bob:
        signup(alice, "journal_alice")
        user = signup(bob, "journal_bob")
        project = create_project(alice)
        root = f"/photodream/api/projects/{project['id']}"
        shot = alice.post(
            "/photodream/api/generate",
            data={"project_id": project["id"], "prompt": "私人海边"},
        ).json()["shot"]
        assert alice.delete(f"{root}/shots/{shot['id']}").status_code == 200
        # Force authorization through the new archive, without preview/audit references.
        with Session(database.get_engine()) as session, session.begin():
            session.execute(
                delete(ProjectEvent).where(ProjectEvent.project_id == project["id"])
            )
        asset = "/photodream" + shot["image_url"] + "?download=true"
        assert alice.get(asset).content == png_bytes()
        assert bob.get(asset).status_code == 404
        forged = create_project(bob, "伪造提示词")
        bob.post(
            f"/photodream/api/projects/{forged['id']}/frames",
            json={"prompt": shot["image_url"]},
        )
        assert bob.get(asset).status_code == 404
        team = alice.post("/photodream/api/teams", json={"name": "日志团队"}).json()
        team_url = f"/photodream/api/teams/{team['id']}"
        invite = alice.post(team_url + "/invites").json()
        assert (
            bob.post(
                "/photodream/api/teams/join", json={"token": invite["token"]}
            ).status_code
            == 200
        )
        assert (
            alice.post(root + "/sharing", json={"team_id": team["id"]}).status_code
            == 200
        )
        items = bob.get("/photodream/api/generations").json()["items"]
        assert len(items) == 1 and items[0]["project_id"] == project["id"]
        assert bob.get(asset).content == png_bytes()
        assert alice.delete(team_url + f"/members/{user['id']}").status_code == 200
        assert bob.get("/photodream/api/generations").json()["items"] == []
        assert bob.get(asset).status_code == 404
