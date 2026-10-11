"""Authorization integration tests use multiple independent browser sessions."""

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from web.app import create_app
from web.models import Project, User, UserSession


def signup(client, username):
    result = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "strong-password-123",
            "display_name": username,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def create_project(client, name="私有项目", team_id=""):
    result = client.post("/api/projects", data={"name": name, "team_id": team_id})
    assert result.status_code == 200, result.text
    return result.json()


def test_login_logout_password_and_csrf(environment):
    with TestClient(create_app()) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/api/projects").status_code == 401
        assert client.get("/outputs/unknown.png").status_code == 401
        assert client.get("/login").status_code == 200
        user = signup(client, "alice")
        assert user["is_admin"] is False
        cookie = client.cookies.get("workflow_session")
        assert len(cookie) >= 40
        assert client.get("/api/auth/me").json()["id"] == user["id"]
        with Session(environment[1]) as session:
            stored = session.get(User, user["id"])
            assert stored.password_hash != "strong-password-123"
            assert session.scalar(select(UserSession.token_hash)) != cookie
        assert (
            client.post(
                "/api/auth/profile",
                json={"display_name": "新名字"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/projects",
                data={"name": "evil"},
                headers={"Sec-Fetch-Site": "cross-site"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/auth/profile", json={"display_name": "新名字"}).json()[
                "display_name"
            ]
            == "新名字"
        )
        assert (
            client.post(
                "/api/auth/password",
                json={
                    "current_password": "wrong",
                    "new_password": "another-password-123",
                },
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/auth/password",
                json={
                    "current_password": "strong-password-123",
                    "new_password": "another-password-123",
                },
            ).status_code
            == 200
        )
        assert client.get("/api/projects").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "strong-password-123"},
            ).status_code
            == 401
        )
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "another-password-123"},
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.get("/").status_code == 200
        assert client.get("/single").status_code == 200
        assert client.get("/storyboard").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        client.cookies.set("workflow_session", cookie)
        assert client.get("/api/auth/me").status_code == 401


def test_personal_project_isolation_and_protected_assets(environment):
    with TestClient(create_app()) as alice, TestClient(create_app()) as bob:
        a = signup(alice, "alice")
        signup(bob, "bob")
        p = create_project(alice)
        assert p["owner_id"] == a["id"] and p["team_id"] is None
        root = f"/api/projects/{p['id']}"
        frame = alice.post(root + "/frames", json={"prompt": "私人提示词"}).json()
        card = alice.post(
            root + "/cards",
            data={"card_type": "character", "name": "私人角色", "content": "私人设定"},
            files={"image": ("private.png", b"test-image", "image/png")},
        ).json()["character_cards"][0]
        assert alice.get(card["image_url"]).content == b"test-image"
        assert bob.get("/api/projects").json() == []
        assert bob.get("/api/resources").json() == []
        assert bob.get(root).status_code == 404
        assert bob.get(root + "/events").status_code == 404
        assert bob.get(root + "/frames").status_code == 404
        assert bob.post(root + "/frames", json={}).status_code == 404
        assert bob.post(root + "/workspace", json={"base_url": "x"}).status_code == 404
        assert (
            bob.post(
                root + f"/frames/{frame['id']}", json={"prompt": "hacked"}
            ).status_code
            == 404
        )
        assert bob.delete(root + f"/frames/{frame['id']}").status_code == 404
        assert bob.post(root + "/sharing", json={"team_id": None}).status_code == 404
        assert bob.get(card["image_url"]).status_code == 404
        # Writing a known URL into a prompt must never grant file access.
        forged = create_project(bob, "伪造引用")
        bob.post(
            f"/api/projects/{forged['id']}/frames", json={"prompt": card["image_url"]}
        )
        bob.post(
            f"/api/projects/{forged['id']}/cards",
            data={"card_type": "scene", "name": "伪造", "content": card["image_url"]},
        )
        assert bob.get(card["image_url"]).status_code == 404
        assert (
            bob.get("/api/debug/state", params={"project_id": p["id"]}).status_code
            == 404
        )
        assert "upload_files" not in bob.get("/api/debug/state").json()
        assert alice.get(root).json()["frames"][0]["prompt"] == "私人提示词"
        assert alice.get(root + "/events").json()[0]["actor_id"] == a["id"]
        with Session(environment[1]) as session, session.begin():
            session.add(
                Project(id="unowned", document={"id": "unowned", "name": "历史项目"})
            )
        assert alice.get("/api/projects/unowned").status_code == 404
        assert bob.get("/api/projects/unowned").status_code == 404


def test_team_sharing_roles_invites_and_immediate_revocation(environment):
    with (
        TestClient(create_app()) as alice,
        TestClient(create_app()) as bob,
        TestClient(create_app()) as charlie,
    ):
        a = signup(alice, "alice")
        b = signup(bob, "bob")
        signup(charlie, "charlie")
        team = alice.post("/api/teams", json={"name": "创作组"}).json()
        t = f"/api/teams/{team['id']}"
        assert bob.get(t + "/members").status_code == 404
        invite = alice.post(t + "/invites").json()
        assert (
            bob.post("/api/teams/join", json={"token": invite["token"]}).status_code
            == 200
        )
        assert (
            charlie.post("/api/teams/join", json={"token": invite["token"]}).status_code
            == 400
        )
        assert bob.get("/api/teams").json()[0]["role"] == "member"
        assert bob.post(t + "/invites").status_code == 403
        assert bob.post(t + "/name", json={"name": "hacked"}).status_code == 403
        p = create_project(bob, "共享项目", team["id"])
        root = f"/api/projects/{p['id']}"
        card = bob.post(
            root + "/cards",
            data={"card_type": "scene", "name": "共享场景", "content": "雨夜"},
            files={"image": ("scene.png", b"shared-image", "image/png")},
        ).json()["scene_cards"][0]
        assert alice.get(root).status_code == 200
        assert alice.get(card["image_url"]).content == b"shared-image"
        assert charlie.get(root).status_code == 404
        assert charlie.get(card["image_url"]).status_code == 404
        assert alice.get("/api/resources").json()[0]["name"] == "共享场景"
        assert bob.post(root + "/sharing", json={"team_id": None}).status_code == 403
        assert bob.post(root + "/metadata", json={"archived": True}).status_code == 403
        assert (
            alice.post(root + "/metadata", json={"name": "重命名"}).status_code == 200
        )
        assert (
            alice.post(
                t + f"/members/{b['id']}/role", json={"role": "admin"}
            ).status_code
            == 200
        )
        assert bob.post(t + "/invites").status_code == 200
        assert (
            bob.post(
                t + f"/members/{a['id']}/role", json={"role": "member"}
            ).status_code
            == 403
        )
        assert bob.delete(t + f"/members/{a['id']}").status_code == 400
        assert (
            alice.post(
                t + f"/members/{b['id']}/role", json={"role": "member"}
            ).status_code
            == 200
        )
        assert alice.delete(t + f"/members/{b['id']}").status_code == 200
        # The original creator also loses access after leaving the team.
        assert bob.get(root).status_code == 404
        assert bob.get(card["image_url"]).status_code == 404
        assert bob.get("/api/resources").json() == []
        assert alice.get(root).status_code == 200
        assert alice.post(root + "/sharing", json={"team_id": None}).status_code == 200
        assert alice.get(root).json()["owner_id"] == a["id"]


def test_resource_import_checks_both_projects_and_keeps_copy(environment):
    with TestClient(create_app()) as alice, TestClient(create_app()) as bob:
        signup(alice, "alice")
        signup(bob, "bob")
        source = create_project(alice, "来源")
        target = create_project(alice, "目标")
        card = alice.post(
            f"/api/projects/{source['id']}/cards",
            data={"card_type": "character", "name": "主角", "content": "蓝色外套"},
            files={"image": ("a.png", b"image", "image/png")},
        ).json()["character_cards"][0]
        payload = {
            "source_project_id": source["id"],
            "card_type": "character",
            "card_ids": [card["id"]],
        }
        assert (
            bob.post(
                f"/api/projects/{target['id']}/resources/import", json=payload
            ).status_code
            == 404
        )
        imported = alice.post(
            f"/api/projects/{target['id']}/resources/import", json=payload
        )
        assert imported.status_code == 200
        assert imported.json()["character_cards"][0]["id"] != card["id"]
        assert imported.json()["character_cards"][0]["image_url"] == card["image_url"]
        team = alice.post("/api/teams", json={"name": "共享目标"}).json()
        token = alice.post(f"/api/teams/{team['id']}/invites").json()["token"]
        bob.post("/api/teams/join", json={"token": token})
        alice.post(
            f"/api/projects/{target['id']}/sharing", json={"team_id": team["id"]}
        )
        assert bob.get(card["image_url"]).status_code == 200
        assert bob.get(f"/api/projects/{source['id']}").status_code == 404


def test_registration_switch_rate_limit_and_admin(environment, monkeypatch):
    with TestClient(create_app()) as client:
        monkeypatch.setenv("ALLOW_REGISTRATION", "false")
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "strong-password-123"},
            ).status_code
            == 403
        )
        monkeypatch.setenv("ALLOW_REGISTRATION", "true")
        assert (
            client.post(
                "/api/auth/register", json={"username": "alice", "password": "short"}
            ).status_code
            == 400
        )
        user = signup(client, "alice")
        assert client.get("/api/admin/users").status_code == 403
        with Session(environment[1]) as session, session.begin():
            session.get(User, user["id"]).is_admin = True
        assert client.get("/api/admin/users").json()[0]["username"] == "alice"
        client.post("/api/auth/logout")
        for _ in range(19):
            client.post(
                "/api/auth/login", json={"username": "missing", "password": "wrong"}
            )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "strong-password-123"},
            ).status_code
            == 429
        )


def test_additive_schema_upgrade_preserves_legacy_projects(environment):
    from sqlalchemy import text
    from web.database import initialize_database

    _, engine = environment
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE projects (id VARCHAR PRIMARY KEY, document JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now())"
            )
        )
        connection.execute(
            text(
                'INSERT INTO projects (id,document) VALUES (\'old\', \'{"id":"old","name":"旧项目"}\')'
            )
        )
    initialize_database()
    initialize_database()
    with Session(engine) as session:
        row = session.get(Project, "old")
        assert row.document["name"] == "旧项目"
        assert row.owner_id is None and row.team_id is None


def test_admin_account_maintenance_revokes_sessions(environment):
    with TestClient(create_app()) as admin, TestClient(create_app()) as user:
        a = signup(admin, "admin")
        u = signup(user, "member")
        with Session(environment[1]) as session, session.begin():
            session.get(User, a["id"]).is_admin = True
        root = f"/api/admin/users/{u['id']}"
        assert user.post(root + "/status", json={"is_active": False}).status_code == 403
        assert (
            admin.post(
                f"/api/admin/users/{a['id']}/status", json={"is_active": False}
            ).status_code
            == 400
        )
        assert (
            admin.post(root + "/status", json={"is_active": False}).status_code == 200
        )
        assert user.get("/api/auth/me").status_code == 401
        assert (
            user.post(
                "/api/auth/login",
                json={"username": "member", "password": "strong-password-123"},
            ).status_code
            == 401
        )
        assert admin.post(root + "/status", json={"is_active": True}).status_code == 200
        assert (
            admin.post(
                root + "/password", json={"password": "reset-password-123"}
            ).status_code
            == 200
        )
        assert (
            user.post(
                "/api/auth/login",
                json={"username": "member", "password": "strong-password-123"},
            ).status_code
            == 401
        )
        assert (
            user.post(
                "/api/auth/login",
                json={"username": "member", "password": "reset-password-123"},
            ).status_code
            == 200
        )
        private = create_project(user)
        assert admin.get(f"/api/projects/{private['id']}").status_code == 404
        created = admin.post(
            "/api/admin/users",
            json={"username": "new_member", "password": "new-password-123"},
        )
        assert created.status_code == 201
        assert (
            admin.post(
                "/api/admin/users",
                json={"username": "new_member", "password": "new-password-123"},
            ).status_code
            == 409
        )
        assert (
            user.post(
                "/api/auth/login",
                json={"username": "new_member", "password": "new-password-123"},
            ).status_code
            == 200
        )
        assert user.get("/api/auth/me").json()["is_admin"] is False


def test_local_admin_initialization_claims_only_unowned_projects(
    environment, monkeypatch
):
    import sys
    from web import manage
    from web.database import initialize_database
    from web.models import ProjectEvent
    from web.repository import ensure_project

    initialize_database()
    legacy = ensure_project("", "历史项目", "")
    with Session(environment[1]) as session, session.begin():
        existing = User(
            id="existing",
            username="existing",
            display_name="原所有者",
            password_hash="unused",
            is_admin=False,
        )
        session.add(existing)
        session.flush()
        session.add(
            Project(
                id="owned",
                owner_id="existing",
                document={"id": "owned", "name": "已有归属项目"},
            )
        )
    monkeypatch.setattr(sys, "argv", ["manage", "admin", "--claim-legacy"])
    monkeypatch.setattr(manage, "getpass", lambda _: "administrator-password-123")
    manage.main()
    with Session(environment[1]) as session:
        user = session.scalar(select(User).where(User.username == "admin"))
        assert user.is_admin and user.is_active
        assert session.get(Project, legacy["id"]).owner_id == user.id
        assert session.get(Project, "owned").owner_id == "existing"
        event = session.scalar(
            select(ProjectEvent).where(ProjectEvent.operation == "project.legacy.claim")
        )
        assert event.project_id == legacy["id"] and event.actor_id == user.id
    with pytest.raises(SystemExit):
        manage.main()
