"""Exception responses, persistence, redaction and administrator review."""

import json
from datetime import datetime, timezone

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from web import app as application, database
from web.errors import UpstreamError
from web.models import ExceptionRecord, ImageGeneration, User
from web.routers import frames, images, system

ADMIN = "/photodream/api/admin/exceptions"


def record_for(response, environment):
    body = response.json()
    assert len(body["detail"]) <= 160
    assert body["detail"] == body["error"]["message"]
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    with Session(environment[1]) as session:
        row = session.get(ExceptionRecord, body["error"]["request_id"])
        assert row is not None
        session.expunge(row)
        return row


def promote(client, environment):
    actor_id = client.get("/photodream/api/auth/me").json()["id"]
    with Session(environment[1]) as session, session.begin():
        session.get(User, actor_id).is_admin = True
    return actor_id


def test_validation_and_middleware_errors_never_capture_inputs(client, environment):
    secret = "super-private-password-input"
    response = client.post(
        "/photodream/api/auth/login",
        json={"username": [], "password": {"sensitive": secret}},
    )
    assert response.status_code == 422
    row = record_for(response, environment)
    assert row.code == "VALIDATION_ERROR"
    assert secret not in response.text + row.diagnostic
    assert "password" in row.diagnostic and "string_type" in row.diagnostic
    response = client.post(
        "/photodream/api/projects",
        data={"name": "cross-site"},
        headers={"origin": "https://other.invalid"},
    )
    assert response.status_code == 403
    assert record_for(response, environment).code == "FORBIDDEN"
    client.cookies.clear()
    response = client.get("/photodream/api/projects")
    assert response.status_code == 401
    assert record_for(response, environment).actor_id is None


def test_unhandled_error_becomes_response_and_records_safe_stack(
    client, environment, monkeypatch
):
    def broken_config():
        raise RuntimeError("never-log-raw-secrets password=hidden-password")

    monkeypatch.setattr(system, "get_settings", broken_config)
    response = client.get("/photodream/api/config?token=secret-query")
    assert response.status_code == 500
    row = record_for(response, environment)
    assert row.code == "INTERNAL_ERROR" and row.actor_id
    assert row.path == "/api/config"
    assert "broken_config" in row.diagnostic
    for secret in ("never-log-raw-secrets", "hidden-password", "secret-query"):
        assert secret not in response.text + row.diagnostic + row.path


@pytest.mark.parametrize(
    "failure,code,status",
    [
        (UpstreamError(401, "invalid_key"), "UPSTREAM_AUTH", 400),
        (UpstreamError(429, "insufficient_quota"), "UPSTREAM_QUOTA", 400),
        (UpstreamError(429, "rate limited"), "UPSTREAM_RATE_LIMIT", 400),
        (UpstreamError(503, "temporarily unavailable"), "UPSTREAM_UNAVAILABLE", 502),
        (UpstreamError(400, "invalid size"), "UPSTREAM_REJECTED", 400),
        (requests.Timeout("timeout"), "UPSTREAM_TIMEOUT", 502),
        (requests.ConnectionError("offline"), "UPSTREAM_CONNECTION", 502),
        (requests.exceptions.SSLError("certificate"), "UPSTREAM_TLS", 502),
    ],
)
def test_generation_error_classification_and_safe_journal(
    client, environment, monkeypatch, failure, code, status
):
    def fail(**kwargs):
        raise failure

    monkeypatch.setattr(images, "relay_generate_raw", fail)
    response = client.post(
        "/photodream/api/generate",
        data={"project_name": "异常测试", "prompt": "测试"},
    )
    assert response.status_code == status
    row = record_for(response, environment)
    assert row.code == code
    with Session(environment[1]) as session:
        generation = session.scalar(select(ImageGeneration))
        assert generation.status == "failed"
        assert generation.error == row.message


def test_frame_failure_preserves_diagnostic_without_exposing_private_profile(
    client, environment, monkeypatch
):
    secret = "unusual-private-credential-947"
    profile = client.post(
        "/photodream/api/settings/api-profiles",
        json={
            "name": "private",
            "base_url": "https://relay.example/v1",
            "api_key": secret,
        },
    ).json()
    project = client.post("/photodream/api/projects", data={"name": "分镜异常"}).json()
    base = f"/photodream/api/projects/{project['id']}/frames"
    frame = client.post(base, json={}).json()

    def fail(**kwargs):
        raise UpstreamError(
            401,
            f"Unauthorized {secret} https://relay.example/?token=hidden-token "
            + "x" * 3000,
        )

    monkeypatch.setattr(frames, "relay_generate_raw", fail)
    response = client.post(
        base + f"/{frame['id']}/generate",
        json={
            "prompt": "敏感提示词不应复制到异常表",
            "api_profile_id": profile["id"],
        },
    )
    assert response.status_code == 400
    row = record_for(response, environment)
    assert row.code == "UPSTREAM_AUTH" and "upstream_status=401" in row.diagnostic
    saved = client.get(base).json()[0]
    assert saved["status"] == "failed" and saved["error"] == row.message
    for value in (
        secret,
        "relay.example",
        "hidden-token",
        "敏感提示词不应复制到异常表",
        "pdenc:v1:",
    ):
        assert value not in response.text + row.diagnostic


def test_admin_review_pagination_filters_resolution_and_isolation(client, environment):
    response = client.get("/photodream/api/projects/missing")
    row = record_for(response, environment)
    for method, path, body in [
        ("get", ADMIN, None),
        ("get", ADMIN + "/" + row.id, None),
        ("post", ADMIN + "/" + row.id + "/resolution", {"resolved": True}),
    ]:
        result = getattr(client, method)(path, **({"json": body} if body else {}))
        assert result.status_code == 403
    actor_id = promote(client, environment)
    # Identical timestamps must not skip rows at pagination boundaries.
    with Session(environment[1]) as session, session.begin():
        for item in session.scalars(select(ExceptionRecord)):
            item.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seen, cursor = set(), ""
    while True:
        page = client.get(ADMIN, params={"limit": 1, "before": cursor}).json()
        assert len(page["items"]) == 1
        entry = page["items"][0]
        assert entry["id"] not in seen
        assert "diagnostic" not in entry and "actor_id" not in entry
        seen.add(entry["id"])
        cursor = page["next_before"]
        if not cursor:
            break
    assert len(seen) == 4
    detail = client.get(ADMIN + "/" + row.id)
    assert "no-store" in detail.headers["cache-control"]
    assert detail.json()["actor_id"] == actor_id
    path = ADMIN + "/" + row.id + "/resolution"
    resolved = client.post(path, json={"resolved": True}).json()
    assert (
        resolved["resolved"]
        and resolved["resolved_by"] == actor_id
        and resolved["resolved_at"]
    )
    page = client.get(
        ADMIN, params={"resolved": "true", "request_id": row.id, "code": row.code}
    ).json()
    assert [item["id"] for item in page["items"]] == [row.id]
    reopened = client.post(path, json={"resolved": False}).json()
    assert not reopened["resolved"] and reopened["resolved_at"] is None
    assert client.get(ADMIN, params={"before": "invalid"}).status_code == 422


def test_db_outage_and_diagnostic_storage_failure_keep_safe_response(
    client, environment, monkeypatch, caplog
):
    def unavailable():
        raise OperationalError(
            "SQL includes private-password", {}, RuntimeError("offline")
        )

    with monkeypatch.context() as patch:
        patch.setattr(system, "get_settings", unavailable)
        patch.setattr(database, "get_engine", unavailable)
        response = client.get("/photodream/api/config")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "Exception persistence unavailable" in caplog.text
    assert "private-password" not in caplog.text + response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_authentication_storage_failure_is_caught_before_mounted_response(
    client, environment, monkeypatch
):
    def broken_session(*args):
        raise RuntimeError("secret auth storage failure")

    monkeypatch.setattr(application, "session_actor", broken_session)
    response = client.get("/photodream/api/projects")
    assert response.status_code == 500
    row = record_for(response, environment)
    assert row.code == "INTERNAL_ERROR" and "broken_session" in row.diagnostic


def test_unmatched_route_and_method_use_uniform_errors(client, environment):
    for method, path, status in [
        ("get", "/photodream/api/does-not-exist", 404),
        ("delete", "/photodream/api/config", 405),
        ("get", "/outside-app", 404),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == status
        assert record_for(response, environment).status_code == status
    assert json.dumps(client.get("/photodream/api/config").json())
