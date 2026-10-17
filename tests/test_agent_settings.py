"""Private Xiao Meng Agent endpoint settings and credential resolution."""

from sqlalchemy.orm import Session

from web.credential_crypto import CIPHERTEXT_PREFIX, decrypt_api_key
from web.models import UserAgentConfig
from web.services import agent as agent_service


ROOT = "/photodream/api/settings/agent"


def save(client, url="https://agent.example/v1", key="agent-secret", model="agent-model"):
    response = client.put(
        ROOT,
        json={"base_url": url, "api_key": key, "model": model},
    )
    assert response.status_code == 200, response.text
    return response


def test_agent_config_crud_is_encrypted_and_key_is_write_only(client, environment):
    initial = client.get(ROOT)
    assert initial.status_code == 200
    assert initial.json()["configured"] is False
    assert "api_key" not in initial.json()

    created = save(client)
    assert created.json() == {
        "configured": True,
        "base_url": "https://agent.example/v1",
        "has_api_key": True,
        "model": "agent-model",
    }
    assert "agent-secret" not in created.text
    loaded = client.get(ROOT)
    assert loaded.json()["has_api_key"] is True
    assert "agent-secret" not in loaded.text

    with Session(environment[1]) as session:
        config = session.query(UserAgentConfig).one()
        assert config.api_key.startswith(CIPHERTEXT_PREFIX)
        assert decrypt_api_key(config) == "agent-secret"

    updated = save(client, model="new-agent-model", key="")
    assert updated.json()["model"] == "new-agent-model"
    with Session(environment[1]) as session:
        assert decrypt_api_key(session.query(UserAgentConfig).one()) == "agent-secret"

    changed_url = client.put(
        ROOT,
        json={
            "base_url": "https://another-agent.example/v1",
            "api_key": "",
            "model": "new-agent-model",
        },
    )
    assert changed_url.status_code == 400
    assert client.delete(ROOT).json() == {"deleted": True}
    assert client.get(ROOT).json()["configured"] is False


def test_agent_config_isolated_between_users(client):
    save(client)
    from fastapi.testclient import TestClient
    from web.app import create_app

    with TestClient(create_app()) as other:
        registered = other.post(
            "/photodream/api/auth/register",
            json={"username": "agent_other", "password": "test-password-123"},
        )
        assert registered.status_code == 201
        assert other.get(ROOT).json()["configured"] is False
        assert other.delete(ROOT).status_code == 200
        assert client.get(ROOT).json()["configured"] is True


def test_agent_chat_uses_user_configured_endpoint_and_model(client, monkeypatch):
    save(client, url="https://custom-agent.example/v1", key="chat-secret", model="chat-model")
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "已收到。"}}]}

    def request(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(agent_service.requests, "post", request)
    session = client.post("/photodream/api/agent/sessions", json={}).json()
    response = client.post(
        f"/photodream/api/agent/sessions/{session['id']}/messages",
        json={"content": "帮我规划一个短片"},
    )
    assert response.status_code == 200
    assert calls[0]["url"] == "https://custom-agent.example/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer chat-secret"
    assert calls[0]["json"]["model"] == "chat-model"
    assert "chat-secret" not in response.text
