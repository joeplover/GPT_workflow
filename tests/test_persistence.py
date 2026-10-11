from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from web.models import ProjectEvent
from web.repository import get_project, list_project_events, mutate_project
from web.routers import frames as routes


def setup_project(client):
    project = client.post("/api/projects", data={"name": "持久化测试"}).json()
    url = f"/api/projects/{project['id']}"
    frame = client.post(f"{url}/frames/initialize").json()[0]
    return project["id"], url, frame


def test_incomplete_frame_settings_are_saved_without_enabling_invalid_generation(
    client,
):
    project_id, url, frame = setup_project(client)
    body = {
        "prompt": "尚未写完的提示词",
        "system_prompt": "画风",
        "model": "",
        "size": "1920x",
    }
    response = client.post(f"{url}/frames/{frame['id']}", json=body)
    assert response.status_code == 200
    saved = get_project(project_id)["frames"][0]
    assert all(saved[key] == value for key, value in body.items())
    assert (
        client.post(f"{url}/frames/{frame['id']}/generate", json=body).status_code
        == 400
    )
    changes = list_project_events(project_id)[0]["changes"]
    assert {tuple(c["path"]): c["after"] for c in changes}[
        ("frames", frame["id"], "prompt")
    ] == body["prompt"]


def test_workspace_updates_merge_and_survive_reload_without_secrets(client):
    project_id, url, frame = setup_project(client)
    card = client.post(
        f"{url}/cards",
        data={"card_type": "character", "name": "主角", "content": "短发"},
    ).json()["character_cards"][0]
    bodies = [
        {"selected_frame_id": frame["id"]},
        {"library_selection": [card["id"], card["id"], "missing"]},
        {"base_url": "http://custom-relay/v1"},
        {"single_image_draft": {"prompt": "单图草稿", "model": "", "size": "未填完"}},
        {
            "card_draft": {
                "card_type": "scene",
                "name": "咖啡馆",
                "content": "尚未提交的场景描述",
            }
        },
    ]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(
            pool.map(lambda body: client.post(f"{url}/workspace", json=body), bodies)
        )
    assert all(r.status_code == 200 for r in results)
    workspace = client.get(url).json()["workspace"]
    assert workspace == get_project(project_id)["workspace"]
    assert workspace["selected_frame_id"] == frame["id"]
    assert workspace["library_selection"] == [card["id"]]
    assert workspace["base_url"] == "http://custom-relay/v1"
    assert workspace["single_image_draft"]["prompt"] == "单图草稿"
    assert workspace["card_draft"]["content"] == "尚未提交的场景描述"
    assert (
        client.post(f"{url}/workspace", json={"api_key": "must-not-store"}).status_code
        == 422
    )
    assert (
        client.post(
            f"{url}/workspace",
            json={"single_image_draft": {"api_key": "must-not-store"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{url}/workspace", json={"selected_frame_id": "missing"}
        ).status_code
        == 400
    )
    assert (
        "must-not-store" not in client.get(url).text + client.get(f"{url}/events").text
    )
    count = len(list_project_events(project_id))
    client.post(f"{url}/workspace", json={"selected_frame_id": frame["id"]})
    assert len(list_project_events(project_id)) == count


def test_delete_event_retains_full_frame_and_generation_versions(client, monkeypatch):
    project_id, url, frame = setup_project(client)
    monkeypatch.setattr(
        routes,
        "relay_generate_raw",
        lambda **kwargs: {"image_url": "/outputs/result.png"},
    )
    response = client.post(
        f"{url}/frames/{frame['id']}/generate",
        json={
            "prompt": "海边",
            "system_prompt": "电影感",
            "api_key": "transient-secret",
        },
    )
    assert response.status_code == 200
    before = response.json()
    assert client.delete(f"{url}/frames/{frame['id']}").json() == []
    events = client.get(f"{url}/events").json()
    assert events[0]["operation"] == "frame.delete"
    deleted = next(
        c for c in events[0]["changes"] if c["path"] == ["frames", frame["id"]]
    )
    assert deleted["before"] == before
    assert deleted["after_exists"] is False
    assert deleted["before"]["versions"][0]["composed_prompt"].startswith(
        "System instructions: 电影感"
    )
    assert "transient-secret" not in client.get(f"{url}/events").text
    assert {e["operation"] for e in events} >= {
        "project.create",
        "frame.initialize",
        "frame.generate.start",
        "frame.generate.complete",
        "frame.delete",
    }
    page = client.get(f"{url}/events?limit=2").json()
    next_page = client.get(f"{url}/events?limit=2&before={page[-1]['id']}").json()
    assert not ({e["id"] for e in page} & {e["id"] for e in next_page})
    assert client.get(f"{url}/events?limit=201").status_code == 422


def test_audit_and_project_mutation_commit_or_rollback_together(client):
    project_id, _, _ = setup_project(client)
    original = get_project(project_id)
    before = list_project_events(project_id)

    def reject_event(mapper, connection, target):
        raise RuntimeError("simulated audit storage failure")

    event.listen(ProjectEvent, "before_insert", reject_event)
    try:
        with pytest.raises(RuntimeError, match="simulated audit storage failure"):
            mutate_project(project_id, lambda project: project.update(name="不该提交"))
    finally:
        event.remove(ProjectEvent, "before_insert", reject_event)
    assert get_project(project_id) == original
    assert list_project_events(project_id) == before


def test_generation_failure_is_recorded_and_previous_content_survives(
    client, monkeypatch
):
    import requests

    project_id, url, frame = setup_project(client)

    def fail(**kwargs):
        raise requests.Timeout("simulated timeout")

    monkeypatch.setattr(routes, "relay_generate_raw", fail)
    response = client.post(
        f"{url}/frames/{frame['id']}/generate", json={"prompt": "失败时也保留"}
    )
    assert response.status_code == 502
    saved = get_project(project_id)["frames"][0]
    assert saved["prompt"] == "失败时也保留"
    assert saved["status"] == "failed"
    assert list_project_events(project_id)[0]["operation"] == "frame.generate.fail"
