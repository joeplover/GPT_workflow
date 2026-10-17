"""Public error messages and private, redacted exception diagnostics."""

from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import os
import re
import traceback
import uuid

from fastapi import HTTPException as FastAPIHTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import requests
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

logger = logging.getLogger("photodream.exceptions")
error_context: ContextVar[dict | None] = ContextVar("error_context", default=None)


def protect_secret(secret):
    context = error_context.get()
    if context is not None and secret:
        context["secrets"].add(secret)


def redact(value, secret="", limit=4000):
    text = str(value)
    secrets = {
        secret,
        os.getenv("PHOTODREAM_ENCRYPTION_KEY", ""),
        os.getenv("HICODE_API_KEY", ""),
        os.getenv("OPENAI_API_KEY", ""),
    }
    context = error_context.get()
    if context:
        secrets.update(context["secrets"])
    for item in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(item, "[已隐藏密钥]")
    text = re.sub(r'(?i)(bearer\s+)[^\s,;"\'<>]+', r"\1[已隐藏密钥]", text)
    text = re.sub(
        r"""(?ix)((?:api[_-]?key|password|token|secret)["']?\s*[:=]\s*["']?)[^\s,;"'<>]+""",
        r"\1[已隐藏密钥]",
        text,
    )
    text = re.sub(r"https?://[^\s<>]+", "[接口地址已隐藏]", text)
    text = re.sub(r"pdenc:v1:[A-Za-z0-9_=-]+", "[密文已隐藏]", text)
    return text[:limit]


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    status: int
    retryable: bool = False


class AgentModelError(FastAPIHTTPException):
    """Keep chat failures distinct even when the cause is a requests timeout."""
    def __init__(self, code, message, status=502, upstream_status=None, diagnostic=""):
        self.info = ErrorInfo(code, message, status, True)
        self.upstream_status = upstream_status
        self.diagnostic = diagnostic
        super().__init__(status, message)


class UpstreamError(FastAPIHTTPException):
    def __init__(self, status, diagnostic=""):
        self.upstream_status = status
        self.diagnostic = diagnostic
        text = diagnostic.lower()
        if status in {401, 403}:
            code, message = (
                "UPSTREAM_AUTH",
                "生图接口认证失败，请在账号设置中检查 API Key 和访问权限。",
            )
        elif any(
            word in text
            for word in ("quota", "balance", "credit", "insufficient", "余额", "额度")
        ):
            code, message = (
                "UPSTREAM_QUOTA",
                "生图接口额度不足，请检查账户余额或更换接口配置。",
            )
        elif status == 429:
            code, message = "UPSTREAM_RATE_LIMIT", "生图请求过于频繁，请稍后重试。"
        elif status >= 500:
            code, message = "UPSTREAM_UNAVAILABLE", "生图服务暂时不可用，请稍后重试。"
        else:
            code, message = (
                "UPSTREAM_REJECTED",
                "生图接口未接受请求，请检查模型、分辨率及提示词。",
            )
        self.info = ErrorInfo(
            code,
            message,
            400 if 400 <= status < 500 else 502,
            status == 429 or status >= 500,
        )
        super().__init__(self.info.status, message)


def describe_error(exc):
    from .credential_crypto import CredentialEncryptionError

    chain, current = [], exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__
    for item in chain:
        if isinstance(item, AgentModelError):
            return item.info
        if isinstance(item, UpstreamError):
            return item.info
        if isinstance(item, requests.exceptions.SSLError):
            return ErrorInfo(
                "UPSTREAM_TLS",
                "无法验证生图接口的安全连接，请检查接口地址和证书。",
                502,
            )
        if isinstance(item, requests.Timeout):
            return ErrorInfo(
                "UPSTREAM_TIMEOUT",
                "生图请求超时，请稍后重试或调整接口超时时间。",
                502,
                True,
            )
        if isinstance(item, requests.ConnectionError):
            return ErrorInfo(
                "UPSTREAM_CONNECTION",
                "无法连接生图接口，请检查接口地址或网络。",
                502,
                True,
            )
        if isinstance(item, IntegrityError):
            return ErrorInfo(
                "DATA_CONFLICT", "数据发生冲突，请刷新页面后重试。", 409, True
            )
        if isinstance(item, SQLAlchemyError):
            return ErrorInfo(
                "DATABASE_UNAVAILABLE", "数据服务暂时不可用，请稍后重试。", 503, True
            )
        if isinstance(item, CredentialEncryptionError):
            return ErrorInfo(
                "CREDENTIAL_UNAVAILABLE",
                "接口 Key 无法解密，请联系管理员检查加密密钥。",
                503,
            )
    if isinstance(exc, RequestValidationError):
        fields = {
            "username": "用户名",
            "password": "密码",
            "name": "名称",
            "prompt": "提示词",
            "model": "模型",
            "size": "分辨率",
            "api_key": "API Key",
            "base_url": "接口地址",
            "new_password": "新密码",
            "current_password": "当前密码",
            "limit": "每页数量",
        }
        names = list(
            dict.fromkeys(
                fields.get(str(e["loc"][-1]), "填写内容") for e in exc.errors()
            )
        )[:3]
        return ErrorInfo(
            "VALIDATION_ERROR",
            "请检查" + "、".join(names or ["填写内容"]) + "的格式或长度。",
            422,
        )
    if isinstance(exc, HTTPException):
        status = exc.status_code
        defaults = {
            400: "请求内容不正确，请检查后重试。",
            401: "登录已失效，请重新登录。",
            403: "没有权限执行此操作。",
            404: "内容不存在或已被删除。",
            405: "不支持此操作。",
            409: "当前资源正在使用，请稍后重试。",
            413: "上传文件过大，请缩小后重试。",
            422: "请检查填写内容。",
            429: "操作过于频繁，请稍后重试。",
            502: "生图服务异常，请稍后重试。",
            503: "服务暂时不可用，请稍后重试。",
            504: "请求超时，请稍后重试。",
        }
        detail = exc.detail if isinstance(exc.detail, str) else ""
        translations = {
            "Prompt is required.": "请先填写提示词。",
            "Missing API key.": "请先在账号设置中添加并选择接口配置。",
        }
        message = translations.get(
            detail, defaults.get(status, "操作失败，请稍后重试。")
        )
        # Only short, server-authored business messages are suitable for the UI.
        if (
            re.search(r"[\u4e00-\u9fff]", detail)
            and len(detail) <= 160
            and not re.search(
                r"https?://|<[^>]+>|Traceback|upstream_|\n|pdenc:", detail
            )
        ):
            message = redact(detail, limit=160)
        code = {
            401: "AUTH_REQUIRED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
        }.get(status, "SERVICE_ERROR" if status >= 500 else "REQUEST_ERROR")
        return ErrorInfo(code, message, status, status in {409, 429, 502, 503, 504})
    return ErrorInfo("INTERNAL_ERROR", "系统暂时出现异常，请稍后重试。", 500, True)


def diagnostics(exc):
    """Never capture request bodies, SQL parameters, cookies or frame locals."""
    parts, current = [], exc
    for _ in range(8):
        if current is None:
            break
        parts.append(type(current).__name__)
        if isinstance(current, (UpstreamError, AgentModelError)) and current.upstream_status is not None:
            parts.append(
                f"upstream_status={current.upstream_status}: {redact(current.diagnostic)}"
            )
        elif isinstance(current, RequestValidationError):
            parts.append(
                json.dumps(
                    [
                        {"field": list(e["loc"]), "type": e["type"]}
                        for e in current.errors()
                    ],
                    ensure_ascii=False,
                )
            )
        elif isinstance(current, (HTTPException, requests.RequestException)):
            parts.append(
                redact(
                    current.detail if isinstance(current, HTTPException) else current
                )
            )
        if current.__traceback__:
            parts.extend(
                f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno} in {frame.name}"
                for frame in traceback.extract_tb(current.__traceback__)
            )
        current = current.__cause__
    return redact("\n".join(parts), limit=12000)


def save_exception(request, exc, info):
    from sqlalchemy.orm import Session
    from . import database
    from .models import ExceptionRecord

    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    request.state.request_id = request_id
    route = request.scope.get("route")
    record = dict(
        id=request_id,
        actor_id=getattr(request.state, "actor_id", None),
        code=info.code,
        message=info.message,
        status_code=info.status,
        method=request.method,
        path=getattr(route, "path", "未知路由"),
        exception_type=type(exc).__name__,
        diagnostic=diagnostics(exc),
    )
    try:
        with Session(database.get_engine()) as session, session.begin():
            session.add(ExceptionRecord(**record))
    except Exception:
        # Database outages must not replace the original response or expose SQL.
        logger.error(
            "Exception persistence unavailable: %s",
            json.dumps(record, ensure_ascii=False),
        )
    return request_id


async def error_response(request: Request, exc):
    info = describe_error(exc)
    request_id = await run_in_threadpool(save_exception, request, exc, info)
    headers = dict(getattr(exc, "headers", None) or {})
    headers.update({"X-Request-ID": request_id, "Cache-Control": "no-store"})
    return JSONResponse(
        {
            "detail": info.message,
            "error": {
                "code": info.code,
                "message": info.message,
                "request_id": request_id,
                "retryable": info.retryable,
            },
        },
        status_code=info.status,
        headers=headers,
    )


def install_exception_handlers(application):
    application.add_exception_handler(HTTPException, error_response)
    application.add_exception_handler(RequestValidationError, error_response)
