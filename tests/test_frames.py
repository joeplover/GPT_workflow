from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import requests
from io import BytesIO
from PIL import Image

from web.repository import get_project, mutate_project
from web.routers import frames as routes


def setup_frames(client, count=3):
    project = client.post("/photodream/api/projects", data={"name": "胶片项目"}).json()
    url = f"/photodream/api/projects/{project['id']}/frames"
    frames = [
        client.post(url, json={"prompt": f"画面{i}"}).json() for i in range(count)
    ]
    return project["id"], url, frames


def test_frame_editing_persistence_and_page(client):
    project_id, url, frames = setup_frames(client)
    assert client.get("/photodream/storyboard").status_code == 200
    assert client.get("/photodream/static/storyboard.js").status_code == 200
    assert client.get(url).json() == frames
    saved = client.post(
        f"{url}/{frames[1]['id']}",
        json={"title": "独立修改", "prompt": "新的提示词", "marked": True},
    )
    assert saved.status_code == 200
    persisted = get_project(project_id)["frames"]
    assert [f["id"] for f in persisted] == [f["id"] for f in frames]
    assert persisted[1]["prompt"] == "新的提示词"
    assert persisted[0]["prompt"] == "画面0"
    assert persisted[1]["marked"] is True
    assert persisted[0]["marked"] is False
    assert client.post(f"{url}/missing", json={}).status_code == 404
    assert client.post(f"{url}/{frames[0]['id']}/generate", json={}).status_code == 400


def test_initialize_one_default_frame_once_even_with_concurrent_opens(
    client, monkeypatch
):
    monkeypatch.setenv("HICODE_IMAGE_MODEL", "my-default-model")
    monkeypatch.setenv("HICODE_IMAGE_SIZE", "1920x1080")
    project = client.post("/photodream/api/projects", data={"name": "新项目"}).json()
    url = f"/photodream/api/projects/{project['id']}/frames"
    assert client.get(url).json() == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post(f"{url}/initialize"), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert all(len(r.json()) == 1 for r in responses)
    frame = responses[0].json()[0]
    assert all(r.json()[0]["id"] == frame["id"] for r in responses)
    assert frame["title"] == "镜头 01"
    assert frame["prompt"] == frame["system_prompt"] == ""
    assert frame["model"] == "my-default-model"
    assert frame["size"] == "1920x1080"
    assert frame["status"] == "draft"
    assert frame["marked"] is False
    assert client.get(url).json() == [frame]
    assert client.post(f"{url}/initialize").json() == [frame]


def test_initialize_preserves_existing_storyboard_and_legacy_project(client):
    project_id, url, frames = setup_frames(client)

    def legacy(project):
        project.pop("frames_initialized", None)

    mutate_project(project_id, legacy)
    assert client.post(f"{url}/initialize").json() == frames
    assert get_project(project_id)["frames"] == frames
    assert (
        client.post("/photodream/api/projects/missing/frames/initialize").status_code
        == 404
    )


def test_delete_frame_preserves_neighbors_cards_and_image_files(client, monkeypatch):
    project_id, url, frames = setup_frames(client)
    from web.services import images

    output = images.OUTPUT_DIR / "shared.png"
    output.write_bytes(b"shared image")
    card = client.post(
        f"/photodream/api/projects/{project_id}/cards",
        data={"card_type": "character", "name": "主角", "content": "蓝衣"},
    ).json()["character_cards"][0]
    monkeypatch.setattr(
        routes,
        "relay_generate_raw",
        lambda **kwargs: {"image_url": "/outputs/shared.png"},
    )
    assert (
        client.post(
            f"{url}/{frames[1]['id']}/generate", json={"prompt": "画面"}
        ).status_code
        == 200
    )
    before = get_project(project_id)
    response = client.delete(f"{url}/{frames[1]['id']}")
    assert response.status_code == 200
    assert response.json() == [before["frames"][0], before["frames"][2]]
    assert client.get(url).json() == response.json()
    assert get_project(project_id)["character_cards"] == [card]
    assert output.read_bytes() == b"shared image"
    assert client.post(f"{url}/{frames[1]['id']}", json={}).status_code == 404
    assert client.delete(f"{url}/{frames[1]['id']}").status_code == 404
    assert (
        client.delete("/photodream/api/projects/missing/frames/missing").status_code
        == 404
    )
    assert client.post(f"{url}/initialize").json() == response.json()


def test_delete_last_frame_stays_empty_until_user_adds_one(client):
    project = client.post("/photodream/api/projects", data={"name": "空项目"}).json()
    url = f"/photodream/api/projects/{project['id']}/frames"
    frame = client.post(f"{url}/initialize").json()[0]
    assert client.delete(f"{url}/{frame['id']}").json() == []
    assert client.get(url).json() == []
    assert client.post(f"{url}/initialize").json() == []
    assert client.post(f"{url}/initialize").json() == []
    added = client.post(url, json={}).json()
    assert added["id"] != frame["id"]
    assert client.get(url).json() == [added]


def test_frames_generate_simultaneously_and_keep_order(client, monkeypatch):
    project_id, url, frames = setup_frames(client)
    client.post(
        f"{url}/{frames[1]['id']}", json={"prompt": frames[1]["prompt"], "marked": True}
    )
    barrier = Barrier(3)

    def relay(**kwargs):
        barrier.wait(timeout=10)
        return {"image_url": f"/outputs/{kwargs['prompt'][-1]}.png"}

    monkeypatch.setattr(routes, "relay_generate_raw", relay)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(
                client.post,
                f"{url}/{f['id']}/generate",
                json={
                    "prompt": f["prompt"],
                    "api_key": "request-secret",
                },
            )
            for f in frames
        ]
        responses = [future.result(timeout=15) for future in futures]
    assert all(r.status_code == 200 for r in responses)
    persisted = get_project(project_id)["frames"]
    assert [f["id"] for f in persisted] == [f["id"] for f in frames]
    assert [f["image_url"] for f in persisted] == [
        "/outputs/0.png",
        "/outputs/1.png",
        "/outputs/2.png",
    ]
    assert all(f["status"] == "ready" for f in persisted)
    assert persisted[1]["marked"] is True
    assert "request-secret" not in client.get(url).text
    # Regenerate a single slot; neighboring images and stable IDs remain intact.
    monkeypatch.setattr(
        routes,
        "relay_generate_raw",
        lambda **kwargs: {"image_url": "/outputs/retry.png"},
    )
    retried = client.post(
        f"{url}/{frames[1]['id']}/generate", json={"prompt": "第二镜的新画面"}
    )
    assert retried.status_code == 200
    assert len(retried.json()["versions"]) == 2
    assert retried.json()["marked"] is True
    persisted = get_project(project_id)["frames"]
    assert [f["image_url"] for f in persisted] == [
        "/outputs/0.png",
        "/outputs/retry.png",
        "/outputs/2.png",
    ]


def test_duplicate_request_is_rejected_and_failure_preserves_previous_image(
    client, monkeypatch
):
    _, url, frames = setup_frames(client, 1)
    frame_url = f"{url}/{frames[0]['id']}"
    monkeypatch.setattr(
        routes,
        "relay_generate_raw",
        lambda **kwargs: {"image_url": "/outputs/previous.png"},
    )
    assert (
        client.post(f"{frame_url}/generate", json={"prompt": "原画面"}).status_code
        == 200
    )
    entered, release = Event(), Event()

    def fail(**kwargs):
        entered.set()
        assert release.wait(timeout=10)
        raise requests.Timeout("test timeout")

    monkeypatch.setattr(routes, "relay_generate_raw", fail)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post, f"{frame_url}/generate", json={"prompt": "修改画面"}
        )
        assert entered.wait(timeout=5)
        try:
            assert client.get(url).json()[0]["status"] == "generating"
            assert (
                client.post(
                    f"{frame_url}/generate", json={"prompt": "重复提交"}
                ).status_code
                == 409
            )
            assert (
                client.post(frame_url, json={"prompt": "生成时改动"}).status_code == 409
            )
            assert client.delete(frame_url).status_code == 409
            assert len(client.get(url).json()) == 1
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 502
    saved = client.get(url).json()[0]
    assert saved["status"] == "failed"
    assert saved["image_url"] == "/outputs/previous.png"
    assert len(saved["versions"]) == 1


def test_expired_generation_can_be_retried(client, monkeypatch):
    project_id, url, frames = setup_frames(client, 1)

    def abandoned(project):
        project["frames"][0].update(
            status="generating",
            generation_id="old-job",
            lease_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )

    mutate_project(project_id, abandoned)
    assert client.get(url).json()[0]["status"] == "failed"
    monkeypatch.setattr(
        routes, "relay_generate_raw", lambda **kwargs: {"image_url": "/outputs/new.png"}
    )
    response = client.post(
        f"{url}/{frames[0]['id']}/generate", json={"prompt": "重新开始"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_apply_cards_and_generate_with_reference_images(client, monkeypatch):
    project_id, url, frames = setup_frames(client)
    image = BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, format="PNG")
    cards = {}
    for card_type, name, description in [
        ("character", "小林", "短发、红色外套"),
        ("scene", "咖啡馆", "暖色光线、木桌"),
    ]:
        project = client.post(
            f"/photodream/api/projects/{project_id}/cards",
            data={"card_type": card_type, "name": name, "content": description},
            files={"image": (f"{card_type}.png", image.getvalue(), "image/png")},
        ).json()
        cards[card_type] = project[f"{card_type}_cards"][0]["id"]
    payload = {
        "frame_ids": [frames[0]["id"], frames[2]["id"]],
        "character_card_ids": [cards["character"]],
        "scene_card_ids": [cards["scene"]],
    }
    response = client.post(f"{url}/apply-cards", json=payload)
    assert response.status_code == 200
    persisted = get_project(project_id)["frames"]
    assert persisted[0]["character_card_ids"] == [cards["character"]]
    assert persisted[2]["scene_card_ids"] == [cards["scene"]]
    assert persisted[1]["character_card_ids"] == []
    calls = []

    def edit(**kwargs):
        calls.append(kwargs)
        with Image.open(kwargs["image_path"]) as collage:
            assert collage.size == (1536, 768)
        return {"image_url": "/outputs/reference.png"}

    monkeypatch.setattr(routes, "relay_edit_raw", edit)
    response = client.post(
        f"{url}/{frames[0]['id']}/generate",
        json={
            **persisted[0],
            "prompt": "坐在桌边",
            "system_prompt": "电影质感，统一暖色调",
            "model": "custom-image-model",
            "size": "1920 × 1080",
        },
    )
    assert response.status_code == 200
    assert "短发、红色外套" in calls[0]["prompt"]
    assert "暖色光线、木桌" in calls[0]["prompt"]
    assert "参考图" in calls[0]["prompt"]
    assert calls[0]["prompt"].startswith("System instructions: 电影质感，统一暖色调")
    assert calls[0]["model"] == "custom-image-model"
    assert calls[0]["size"] == "1920x1080"
    assert response.json()["system_prompt"] == "电影质感，统一暖色调"
    assert response.json()["versions"][0]["system_prompt"] == "电影质感，统一暖色调"
    # Invalid bulk selection must roll back all assignments.
    payload["frame_ids"].append("missing")
    payload["character_card_ids"] = []
    assert client.post(f"{url}/apply-cards", json=payload).status_code == 404
    assert get_project(project_id)["frames"][0]["character_card_ids"] == [
        cards["character"]
    ]
    payload["frame_ids"] = [frames[0]["id"]]
    payload["scene_card_ids"] = ["missing-card"]
    assert client.post(f"{url}/apply-cards", json=payload).status_code == 400
