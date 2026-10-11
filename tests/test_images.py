import base64
from io import BytesIO
from unittest.mock import MagicMock

from PIL import Image
import requests

from web.routers import images as routes
from web.services import images


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


def test_generation_upload_edit_and_continuity(client, monkeypatch):
    calls = []

    def relay(**kwargs):
        calls.append(kwargs)
        output = images.OUTPUT_DIR / f"result_{len(calls)}.png"
        output.write_bytes(png_bytes())
        return {"image_url": f"/outputs/{output.name}"}

    monkeypatch.setattr(routes, "relay_generate_raw", relay)
    monkeypatch.setattr(routes, "relay_edit_raw", relay)
    response = client.post(
        "/api/generate",
        data={
            "project_name": "图片测试",
            "prompt": "海边",
            "system_prompt": "电影感",
            "model": "my-custom-model",
            "size": "1920 × 1080",
        },
    )
    assert response.status_code == 200
    first = response.json()
    assert first["shot"]["mode"] == "generate"
    assert first["shot"]["system_prompt"] == "电影感"
    assert calls[0]["prompt"].startswith("System instructions: 电影感")
    assert calls[0]["model"] == "my-custom-model"
    assert calls[0]["size"] == "1920x1080"
    assert client.get(first["shot"]["image_url"]).content == png_bytes()
    project_id = first["project"]["id"]
    card = client.post(
        f"/api/projects/{project_id}/cards",
        data={
            "card_type": "character",
            "name": "角色",
            "content": "蓝衣服",
        },
        files={"image": ("character.png", png_bytes(), "image/png")},
    )
    assert card.status_code == 200
    character = card.json()["character_cards"][0]
    assert client.get(character["image_url"]).content == png_bytes()
    response = client.post(
        "/api/generate",
        data={
            "project_id": project_id,
            "prompt": "走向海边",
            "character_card_ids": character["id"],
        },
        files=[("reference_images", ("ref.png", png_bytes(), "image/png"))],
    )
    assert response.status_code == 200
    shot = response.json()["shot"]
    assert shot["mode"] == "reference_generate"
    assert len(shot["reference_images"]) == 2
    assert "蓝衣服" in calls[-1]["prompt"]
    with Image.open(calls[-1]["image_path"]) as collage:
        assert collage.size == (1536, 768)
    response = client.post(
        "/api/generate",
        data={
            "project_id": project_id,
            "prompt": "下一镜",
            "continue_from_last": "true",
        },
    )
    assert response.status_code == 200
    assert response.json()["shot"]["mode"] == "continue"
    response = client.post(
        "/api/edit",
        data={"project_id": project_id, "prompt": "修改天空"},
        files={
            "image": ("image.png", png_bytes(), "image/png"),
            "mask": ("mask.png", png_bytes(), "image/png"),
        },
    )
    assert response.status_code == 200
    assert response.json()["shot"]["mode"] == "edit"
    assert calls[-1]["mask_path"].is_file()
    assert len(response.json()["project"]["shots"]) == 4


def test_invalid_requests_and_upstream_errors(client, monkeypatch):
    assert client.post("/api/generate", data={"prompt": " "}).status_code == 400
    assert (
        client.post("/api/generate", data={"prompt": "test", "size": "bad"}).status_code
        == 400
    )
    assert client.post("/api/edit", data={"prompt": "test"}).status_code == 400
    monkeypatch.setenv("HICODE_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.post("/api/generate", data={"prompt": "test"}).status_code == 400
    monkeypatch.setenv("HICODE_API_KEY", "test")

    def failing_relay(**kwargs):
        raise requests.Timeout("test timeout")

    monkeypatch.setattr(routes, "relay_generate_raw", failing_relay)
    assert client.post("/api/generate", data={"prompt": "test"}).status_code == 502


def test_relay_base64_persistence_and_session_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "OUTPUT_DIR", tmp_path)
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]
    }
    session = MagicMock()
    session.__enter__.return_value = session
    session.post.return_value = response
    monkeypatch.setattr(images, "build_session", lambda use_env_proxy: session)
    result = images.relay_generate_raw(
        "test", "model", "1024x1024", "key", "http://relay/v1", True, False, 30
    )
    assert (tmp_path / result["image_url"].split("/")[-1]).read_bytes() == png_bytes()
    assert session.post.call_args.kwargs["timeout"] == 30
    session.__exit__.assert_called_once()
