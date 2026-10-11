"""Verify complete prompts at the outgoing HTTP boundary, without paid calls."""

import base64
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
from threading import Barrier, Lock

from PIL import Image
import pytest
import requests

from web.repository import get_project
from web.services import images
from web.services.storyboard import build_composed_prompt


CONSISTENCY = (
    "[重要指令] 请严格保持参考图中所有角色的外貌、服装、面部特征完全一致。"
    "将角色自然地融入到当前镜头描述的场景中。不要改变角色的样貌。"
)


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def outgoing(monkeypatch):
    """Capture requests after JSON/multipart encoding; never open a socket."""
    calls = []
    lock = Lock()

    class FakeRelay(requests.Session):
        def send(self, request, **kwargs):
            if request.headers["Content-Type"].startswith("application/json"):
                fields = json.loads(request.body)
                files = {}
            else:
                message = BytesParser(policy=default).parsebytes(
                    f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
                    + request.body
                )
                fields, files = {}, {}
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    content = part.get_payload(decode=True)
                    if part.get_filename():
                        files[name] = content
                    else:
                        fields[name] = content.decode("utf-8")
            with lock:
                calls.append({"url": request.url, "fields": fields, "files": files})
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(
                {"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
            ).encode()
            return response

    def session(use_env_proxy):
        result = FakeRelay()
        result.trust_env = False
        return result

    monkeypatch.setattr(images, "build_session", session)
    return calls


def test_full_prompt_order_selection_deduplication_and_multiline():
    project = {
        "synopsis": "  雨夜故事\n城市背景  ",
        "character_cards": [
            {"id": "a", "name": "小林", "content": "短发\n红色外套"},
            {"id": "b", "name": "小周", "content": "蓝色围巾"},
            {"id": "c", "name": "无关角色", "content": "不能出现在提示词里"},
        ],
        "scene_cards": [
            {"id": "s", "name": "咖啡馆", "content": "暖光、木桌"},
            {"id": "t", "name": "无关场景", "content": "不能出现在提示词里"},
        ],
    }
    actual = build_composed_prompt(
        project,
        "  两人交谈\n近景  ",
        ["b", "a", "a", "missing"],
        ["s", "s"],
        has_reference_images=True,
        system_prompt="  电影质感\n统一色调  ",
    )
    assert actual == (
        "System instructions: 电影质感\n统一色调\n"
        f"{CONSISTENCY}\n"
        "Project setup: 雨夜故事\n城市背景\n"
        "Character cards: 小林: 短发\n红色外套 | 小周: 蓝色围巾\n"
        "Scene cards: 咖啡馆: 暖光、木桌\n"
        "Current shot: 两人交谈\n近景"
    )


@pytest.mark.parametrize("system_prompt", ["", " \n\t "])
@pytest.mark.parametrize("has_reference", [False, True])
def test_empty_optional_sections(system_prompt, has_reference):
    assert (
        build_composed_prompt(
            {"synopsis": " \n "},
            "  海边\n远景  ",
            [],
            [],
            has_reference_images=has_reference,
            system_prompt=system_prompt,
        )
        == (f"{CONSISTENCY}\n" if has_reference else "") + "Current shot: 海边\n远景"
    )


def create_project_and_cards(client, image_card_type=None):
    project = client.post(
        "/photodream/api/projects", data={"name": "拼接测试", "synopsis": "雨夜故事"}
    ).json()
    selected = {}
    for card_type, name, content in [
        ("character", "小林", "短发、红色外套"),
        ("scene", "咖啡馆", "暖光、木桌"),
        ("character", "未选角色", "不应传给模型"),
        ("scene", "未选场景", "不应传给模型"),
    ]:
        response = client.post(
            f"/photodream/api/projects/{project['id']}/cards",
            data={"card_type": card_type, "name": name, "content": content},
            files={"image": ("ref.png", png_bytes(), "image/png")}
            if card_type == image_card_type and not name.startswith("未选")
            else None,
        )
        assert response.status_code == 200
        if not name.startswith("未选"):
            selected[card_type] = response.json()[f"{card_type}_cards"][-1]["id"]
    return project["id"], selected


@pytest.mark.parametrize("endpoint", ["frame", "generate", "edit"])
@pytest.mark.parametrize("image_card_type", [None, "character", "scene"])
def test_complete_prompt_survives_http_encoding_and_history(
    client, outgoing, endpoint, image_card_type
):
    project_id, selected = create_project_and_cards(client, image_card_type)
    body = {
        "prompt": "  坐在桌边\n看向窗外  ",
        "system_prompt": "  电影质感\n统一暖色调  ",
        "model": "custom-model",
        "size": "1920 × 1080",
        "character_card_ids": [selected["character"]],
        "scene_card_ids": [selected["scene"]],
    }
    if endpoint == "frame":
        url = f"/photodream/api/projects/{project_id}/frames"
        frame = client.post(url, json=body).json()
        response = client.post(f"{url}/{frame['id']}/generate", json=body)
    else:
        response = client.post(
            f"/photodream/api/{endpoint}",
            data={
                **body,
                "character_card_ids": selected["character"],
                "scene_card_ids": selected["scene"],
                "project_id": project_id,
                "synopsis": "雨夜故事",
            },
            files={"image": ("main.png", png_bytes(), "image/png")}
            if endpoint == "edit"
            else None,
        )
    assert response.status_code == 200, response.text
    expected = (
        "System instructions: 电影质感\n统一暖色调\n"
        + (f"{CONSISTENCY}\n" if image_card_type else "")
        + "Project setup: 雨夜故事\n"
        "Character cards: 小林: 短发、红色外套\n"
        "Scene cards: 咖啡馆: 暖光、木桌\n"
        "Current shot: 坐在桌边\n看向窗外"
    )
    assert len(outgoing) == 1
    call = outgoing[0]
    assert call["fields"] == {
        "model": "custom-model",
        "size": "1920x1080",
        "prompt": expected,
    }
    is_edit = endpoint == "edit" or image_card_type is not None
    assert call["url"].endswith("/images/edits" if is_edit else "/images/generations")
    assert bool(call["files"].get("image")) == is_edit
    saved = get_project(project_id)
    history = (
        saved["frames"][0]["versions"][0] if endpoint == "frame" else saved["shots"][0]
    )
    assert history["composed_prompt"] == expected
    assert history["system_prompt"].strip() == body["system_prompt"].strip()
    assert history["prompt"].strip() == body["prompt"].strip()
    assert saved["synopsis"] == "雨夜故事"


def test_concurrent_frames_do_not_mix_prompts_or_card_selections(
    client, outgoing, monkeypatch
):
    project_id, selected = create_project_and_cards(client)
    url = f"/photodream/api/projects/{project_id}/frames"
    bodies = [
        {
            "prompt": "海边远景",
            "system_prompt": "冷色调",
            "character_card_ids": [selected["character"]],
        },
        {
            "prompt": "窗边近景",
            "system_prompt": "暖色调",
            "scene_card_ids": [selected["scene"]],
        },
    ]
    frames = [client.post(url, json=body).json() for body in bodies]
    build_session = images.build_session
    barrier = Barrier(2)

    def synchronized_session(use_env_proxy):
        barrier.wait(timeout=10)
        return build_session(use_env_proxy)

    monkeypatch.setattr(images, "build_session", synchronized_session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(client.post, f"{url}/{frame['id']}/generate", json=body)
            for frame, body in zip(frames, bodies)
        ]
        assert all(f.result(timeout=15).status_code == 200 for f in futures)
    expected = [
        "System instructions: 冷色调\nProject setup: 雨夜故事\nCharacter cards: 小林: 短发、红色外套\nCurrent shot: 海边远景",
        "System instructions: 暖色调\nProject setup: 雨夜故事\nScene cards: 咖啡馆: 暖光、木桌\nCurrent shot: 窗边近景",
    ]
    assert {call["fields"]["prompt"] for call in outgoing} == set(expected)
    persisted = get_project(project_id)["frames"]
    assert [f["versions"][0]["composed_prompt"] for f in persisted] == expected


def test_regenerate_after_changing_only_system_prompt_preserves_previous_version(
    client, outgoing
):
    project_id, selected = create_project_and_cards(client)
    url = f"/photodream/api/projects/{project_id}/frames"
    body = {"prompt": "海边", "system_prompt": "冷色调"}
    frame = client.post(url, json=body).json()
    generate_url = f"{url}/{frame['id']}/generate"
    first = client.post(generate_url, json=body)
    assert first.status_code == 200
    second = client.post(generate_url, json={**body, "system_prompt": "暖色调"})
    assert second.status_code == 200
    assert [call["fields"]["prompt"] for call in outgoing] == [
        "System instructions: 冷色调\nProject setup: 雨夜故事\nCurrent shot: 海边",
        "System instructions: 暖色调\nProject setup: 雨夜故事\nCurrent shot: 海边",
    ]
    versions = second.json()["versions"]
    assert [version["system_prompt"] for version in versions] == ["暖色调", "冷色调"]
    assert [version["composed_prompt"] for version in versions] == [
        outgoing[1]["fields"]["prompt"],
        outgoing[0]["fields"]["prompt"],
    ]


@pytest.mark.parametrize("endpoint", ["generate", "edit"])
def test_legacy_generation_should_preserve_omitted_project_background(
    client, outgoing, endpoint
):
    project_id, _ = create_project_and_cards(client)
    response = client.post(
        f"/photodream/api/{endpoint}",
        data={"project_id": project_id, "prompt": "海边"},
        files={"image": ("main.png", png_bytes(), "image/png")}
        if endpoint == "edit"
        else None,
    )
    assert response.status_code == 200
    assert get_project(project_id)["synopsis"] == "雨夜故事"
    assert "Project setup: 雨夜故事" in outgoing[0]["fields"]["prompt"]


@pytest.mark.xfail(
    strict=True, reason="Continuation reference is excluded from has_reference_images"
)
def test_continuation_should_include_reference_consistency_instruction(
    client, outgoing
):
    first = client.post("/photodream/api/generate", data={"prompt": "海边"})
    assert first.status_code == 200
    response = client.post(
        "/photodream/api/generate",
        data={
            "project_id": first.json()["project"]["id"],
            "prompt": "下一镜",
            "system_prompt": "统一画风",
            "continue_from_last": "true",
        },
    )
    assert response.status_code == 200
    assert response.json()["shot"]["mode"] == "continue"
    assert outgoing[-1]["files"]["image"] == png_bytes()
    assert outgoing[-1]["fields"]["prompt"] == (
        f"System instructions: 统一画风\n{CONSISTENCY}\nCurrent shot: 下一镜"
    )
