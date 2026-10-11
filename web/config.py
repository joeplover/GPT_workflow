"""Environment configuration shared by the API and image services."""

import os
from math import gcd
from pathlib import Path
from typing import Any
from fastapi import HTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUNTIME_DIR = Path(
    os.getenv("WORKFLOW_RUNTIME_DIR", str(BASE_DIR / "runtime"))
).resolve()
OUTPUT_DIR = RUNTIME_DIR / "outputs"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
PROJECTS_FILE = RUNTIME_DIR / "projects.json"

# Presets are suggestions; the upstream image service determines supported sizes.
COMMON_SIZES = (
    "512x512",
    "1024x1024",
    "2048x2048",
    "1280x720",
    "1920x1080",
    "2048x1152",
    "2560x1440",
    "3840x2160",
    "720x1280",
    "1080x1920",
    "1152x2048",
    "1440x2560",
    "2160x3840",
    "1024x768",
    "1600x1200",
    "2048x1536",
    "768x1024",
    "1200x1600",
    "1536x2048",
    "1536x1024",
    "2400x1600",
    "3072x2048",
    "1024x1536",
    "1600x2400",
    "2048x3072",
    "1080x1350",
    "1600x2000",
    "2048x2560",
    "1350x1080",
    "2000x1600",
    "2560x2048",
    "2520x1080",
    "3360x1440",
    "1080x2520",
    "1440x3360",
    "2048x1024",
    "1024x2048",
    "1920x1200",
    "2560x1600",
    "1200x1920",
    "1600x2560",
)


def size_presets() -> list[dict[str, str]]:
    presets = []
    for size in COMMON_SIZES:
        width, height = map(int, size.split("x"))
        divisor = gcd(width, height)
        ratio = f"{width // divisor}:{height // divisor}"
        # Widescreen convention: 21:9 is the same ratio as 7:3.
        ratio = {"7:3": "21:9", "3:7": "9:21", "8:5": "16:10", "5:8": "10:16"}.get(
            ratio, ratio
        )
        presets.append(
            {"size": size, "ratio": ratio, "label": f"{ratio} · {width} × {height}"}
        )
    return presets


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required. Configure a PostgreSQL connection; see README.md."
        )
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must use PostgreSQL (postgresql+psycopg://).")
    return url


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def get_settings() -> dict[str, Any]:
    return {
        "api_key": os.getenv("HICODE_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
        "base_url": os.getenv("HICODE_BASE_URL", "https://api.hi-code.cc/v1").rstrip(
            "/"
        ),
        "default_model": os.getenv("HICODE_IMAGE_MODEL", "gpt-image-2"),
        "default_size": os.getenv("HICODE_IMAGE_SIZE", "2048x1152"),
        "verify_ssl": os.getenv("HICODE_VERIFY_SSL", "true").lower() != "false",
        "use_env_proxy": os.getenv("HICODE_USE_ENV_PROXY", "false").lower() == "true",
        "request_timeout": positive_int("HICODE_REQUEST_TIMEOUT", 300),
        "max_project_shots": positive_int("HICODE_MAX_PROJECT_SHOTS", 200),
    }


def resolve_runtime_settings(
    api_key: str | None, base_url: str | None
) -> dict[str, Any]:
    settings = get_settings()
    requested_url = (base_url or "").strip().rstrip("/") or settings["base_url"].rstrip(
        "/"
    )
    if (
        not (api_key or "").strip()
        and settings["api_key"]
        and requested_url != settings["base_url"].rstrip("/")
    ):
        raise HTTPException(
            400,
            "使用服务端 Key 时只能使用服务端配置的接口地址；自定义接口请填写自己的 API Key。",
        )
    return {
        **settings,
        "api_key": (api_key or "").strip() or settings["api_key"],
        "base_url": (base_url or "").strip().rstrip("/") or settings["base_url"],
    }
