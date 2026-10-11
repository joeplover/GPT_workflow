import pytest

from web.config import (
    database_url,
    get_settings,
    resolve_runtime_settings,
    size_presets,
)
from web.services.storyboard import normalize_generation_args, parse_screenplay_to_plan


def test_database_url_requires_postgres(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="required"):
        database_url()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        database_url()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/workflow")
    assert database_url() == "postgresql+psycopg://user:password@localhost/workflow"


def test_invalid_numeric_settings_use_defaults(monkeypatch):
    monkeypatch.setenv("HICODE_REQUEST_TIMEOUT", "invalid")
    monkeypatch.setenv("HICODE_MAX_PROJECT_SHOTS", "-1")
    assert get_settings()["request_timeout"] == 300
    assert get_settings()["max_project_shots"] == 200


def test_screenplay_parsing_and_size_validation():
    plan = parse_screenplay_to_plan("1. 门打开\n\n2、角色走出\n")
    assert [item["prompt_seed"] for item in plan] == ["门打开", "角色走出"]
    assert [item["order"] for item in plan] == [1, 2]
    assert (
        normalize_generation_args(" gpt-image-2 ", "1024x1024")["model"]
        == "gpt-image-2"
    )
    with pytest.raises(Exception) as error:
        normalize_generation_args("gpt-image-2", "bad")
    assert error.value.status_code == 400


def test_custom_sizes_and_aspect_ratio_presets():
    assert normalize_generation_args(" custom-model ", " 1920 × 1080 ") == {
        "model": "custom-model",
        "size": "1920x1080",
    }
    assert normalize_generation_args("model", "2560X1600")["size"] == "2560x1600"
    with pytest.raises(Exception) as error:
        normalize_generation_args("model", "1920x1080junk")
    assert error.value.status_code == 400
    presets = size_presets()
    ratios = {item["ratio"] for item in presets}
    assert {
        "1:1",
        "16:9",
        "9:16",
        "4:3",
        "3:4",
        "3:2",
        "2:3",
        "4:5",
        "5:4",
        "21:9",
        "16:10",
        "10:16",
    } <= ratios
    assert (
        next(item for item in presets if item["size"] == "3840x2160")["label"]
        == "16:9 · 3840 × 2160"
    )


def test_server_api_key_cannot_be_sent_to_custom_endpoint(monkeypatch):
    monkeypatch.setenv("HICODE_API_KEY", "private-server-key")
    monkeypatch.setenv("HICODE_BASE_URL", "https://relay.example/v1")
    with pytest.raises(Exception) as error:
        resolve_runtime_settings("", "https://other.example/v1")
    assert error.value.status_code == 400
    assert (
        resolve_runtime_settings("", "https://relay.example/v1/")["api_key"]
        == "private-server-key"
    )
    assert (
        resolve_runtime_settings("user-key", "https://other.example/v1")["api_key"]
        == "user-key"
    )
