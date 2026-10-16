"""Private credential persistence, ownership and real generation resolution."""

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from web.app import create_app
from web.credential_crypto import CIPHERTEXT_PREFIX, decrypt_api_key
from web.models import ImageGeneration, ProjectEvent, User, UserApiProfile
from web.routers import frames, images

ROOT = "/photodream/api/settings/api-profiles"


def add_profile(
    client, name="主接口", url="https://primary.example/v1", key="private-profile-key"
):
    response = client.post(ROOT, json={"name": name, "base_url": url, "api_key": key})
    assert response.status_code == 201, response.text
    assert key not in response.text
    return response.json()


def test_profile_crud_selection_and_persistence(client, environment):
    first = add_profile(client)
    second = add_profile(client, "备用接口", "https://backup.example/v1", "backup-key")
    assert first["selected"] is True and second["selected"] is False
    result = client.get(ROOT)
    assert "no-store" in result.headers["cache-control"]
    assert result.json()["selected_id"] == first["id"]
    assert "private-profile-key" not in result.text and "backup-key" not in result.text
    assert (
        client.post(ROOT + "/selection", json={"profile_id": second["id"]}).status_code
        == 200
    )
    assert client.get(ROOT).json()["selected_id"] == second["id"]
    assert (
        client.post(
            ROOT + "/" + first["id"],
            json={
                "name": "已重命名",
                "base_url": first["base_url"],
                "api_key": "",
            },
        ).status_code
        == 200
    )
    with Session(environment[1]) as session:
        row = session.get(UserApiProfile, first["id"])
        assert row.api_key.startswith(CIPHERTEXT_PREFIX)
        assert "private-profile-key" not in row.api_key
        assert decrypt_api_key(row) == "private-profile-key" and row.name == "已重命名"
    assert (
        client.post(
            ROOT + "/" + first["id"],
            json={
                "name": "已重命名",
                "base_url": "https://new.example/v1",
                "api_key": "",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            ROOT + "/" + first["id"],
            json={
                "name": "已重命名",
                "base_url": "https://new.example/v1/",
                "api_key": "new-key",
            },
        ).status_code
        == 200
    )
    with Session(environment[1]) as session:
        row = session.get(UserApiProfile, first["id"])
        assert row.api_key.startswith(CIPHERTEXT_PREFIX)
        assert row.api_key != "new-key"
        assert decrypt_api_key(row) == "new-key"
        assert row.base_url == "https://new.example/v1"
    # A separate authenticated browser session sees the same persisted selection.
    with TestClient(create_app()) as other:
        assert (
            other.post(
                "/photodream/api/auth/login",
                json={
                    "username": "test_owner",
                    "password": "test-password-123",
                },
            ).status_code
            == 200
        )
        assert other.get(ROOT).json()["selected_id"] == second["id"]
        assert other.delete(ROOT + "/" + second["id"]).status_code == 200
        assert other.get(ROOT).json()["selected_id"] == ""
    assert (
        client.post(ROOT + "/selection", json={"profile_id": second["id"]}).status_code
        == 404
    )


def test_profiles_isolated_even_for_team_members_and_admins(client, environment):
    profile = add_profile(client)
    team = client.post("/photodream/api/teams", json={"name": "共享团队"}).json()
    invite = client.post(f"/photodream/api/teams/{team['id']}/invites", json={}).json()
    project = client.post(
        "/photodream/api/projects", data={"name": "共享项目", "team_id": team["id"]}
    ).json()
    with TestClient(create_app()) as bob:
        user = bob.post(
            "/photodream/api/auth/register",
            json={
                "username": "profile_bob",
                "password": "test-password-123",
            },
        ).json()
        with Session(environment[1]) as session, session.begin():
            session.get(User, user["id"]).is_admin = True
        assert (
            bob.post(
                "/photodream/api/teams/join", json={"token": invite["token"]}
            ).status_code
            == 200
        )
        assert bob.get(f"/photodream/api/projects/{project['id']}").status_code == 200
        assert bob.get(ROOT).json() == {"profiles": [], "selected_id": ""}
        for url, payload in [
            (ROOT + "/selection", {"profile_id": profile["id"]}),
            (
                ROOT + "/" + profile["id"],
                {"name": "偷用", "base_url": profile["base_url"]},
            ),
        ]:
            assert bob.post(url, json=payload).status_code == 404
        assert bob.delete(ROOT + "/" + profile["id"]).status_code == 404
        assert (
            bob.post(
                "/photodream/api/generate",
                data={
                    "project_id": project["id"],
                    "prompt": "共享镜头",
                    "api_profile_id": profile["id"],
                },
            ).status_code
            == 404
        )
        own = add_profile(bob, key="bobs-private-key")
        assert own["id"] != profile["id"]
    assert len(client.get(ROOT).json()["profiles"]) == 1


def test_profile_validation_and_no_silent_fallback(client):
    for url in [
        "file:///tmp/key",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/#key",
        "http://[broken",
        "https://example.com:bad/v1",
    ]:
        assert (
            client.post(
                ROOT, json={"name": "test", "base_url": url, "api_key": "secret"}
            ).status_code
            == 400
        )
    for name, key in [(" ", "secret"), ("test", ""), ("test", "abc\nxyz")]:
        assert (
            client.post(
                ROOT,
                json={
                    "name": name,
                    "base_url": "https://example.com/v1",
                    "api_key": key,
                },
            ).status_code
            == 400
        )
    add_profile(client)
    assert (
        client.post(
            ROOT,
            json={
                "name": "主接口",
                "base_url": "https://other.example/v1",
                "api_key": "secret",
            },
        ).status_code
        == 409
    )
    for path in ["/photodream/api/generate", "/photodream/api/edit"]:
        assert (
            client.post(
                path, data={"prompt": "test", "api_profile_id": "deleted-profile"}
            ).status_code
            == 404
        )


def test_generation_uses_private_pair_and_does_not_persist_credentials(
    client, environment, monkeypatch
):
    profile = add_profile(client)
    calls = []

    def relay(**kwargs):
        calls.append(kwargs)
        return {"image_url": f"/outputs/private-profile-{len(calls)}.png"}

    for module in (images, frames):
        monkeypatch.setattr(module, "relay_generate_raw", relay)
        monkeypatch.setattr(module, "relay_edit_raw", relay)
    project = client.post(
        "/photodream/api/projects", data={"name": "私有接口测试"}
    ).json()
    body = {
        "project_id": project["id"],
        "prompt": "城市",
        "api_profile_id": profile["id"],
        "base_url": "https://override.example/v1",
        "api_key": "override-key",
    }
    assert client.post("/photodream/api/generate", data=body).status_code == 200
    buffer = BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, format="PNG")
    assert (
        client.post(
            "/photodream/api/edit",
            data=body,
            files={"image": ("ref.png", buffer.getvalue(), "image/png")},
        ).status_code
        == 200
    )
    frame_root = f"/photodream/api/projects/{project['id']}/frames"
    frame = client.post(frame_root, json={"prompt": "街道"}).json()
    result = client.post(frame_root + f"/{frame['id']}/generate", json=body)
    assert result.status_code == 200, result.text
    assert len(calls) == 3
    assert all(
        c["api_key"] == "private-profile-key" and c["base_url"] == profile["base_url"]
        for c in calls
    )
    stored = client.get(f"/photodream/api/projects/{project['id']}").text
    with Session(environment[1]) as session:
        stored += repr(list(session.scalars(select(ProjectEvent.changes))))
        stored += repr(list(session.scalars(select(ImageGeneration.request))))
    for forbidden in [
        "private-profile-key",
        "override-key",
        "api_profile_id",
        profile["id"],
    ]:
        assert forbidden not in stored
    client.delete(ROOT + "/" + profile["id"])
    assert (
        client.post(frame_root + f"/{frame['id']}/generate", json=body).status_code
        == 404
    )
    assert len(calls) == 3


def test_private_key_redacted_from_upstream_failure(
    client, environment, monkeypatch, capsys
):
    profile = add_profile(client)

    def fail(**kwargs):
        raise requests.Timeout("relay rejected private-profile-key")

    monkeypatch.setattr(images, "relay_generate_raw", fail)
    response = client.post(
        "/photodream/api/generate",
        data={
            "project_name": "接口错误",
            "prompt": "test",
            "api_profile_id": profile["id"],
        },
    )
    assert response.status_code == 502
    assert "private-profile-key" not in response.text
    assert "private-profile-key" not in capsys.readouterr().out
    with Session(environment[1]) as session:
        assert "private-profile-key" not in repr(
            list(session.scalars(select(ImageGeneration.error)))
        )
