"""File handling and synchronous relay calls, run in FastAPI worker threads."""

import base64
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ..config import OUTPUT_DIR, UPLOAD_DIR, get_settings


def build_session(use_env_proxy: bool) -> requests.Session:
    session = requests.Session()
    session.trust_env = use_env_proxy
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def local_path_from_url(url: str) -> Optional[Path]:
    if not url:
        return None
    if url.startswith("/outputs/"):
        path = OUTPUT_DIR / Path(url).name
        return path if path.exists() else None
    if url.startswith("/uploads/"):
        path = UPLOAD_DIR / Path(url).name
        return path if path.exists() else None
    return None


def save_upload_file(
    upload: UploadFile, suffix_fallback: str = ".png"
) -> Optional[Path]:
    if not upload or not upload.filename:
        return None
    ext = Path(upload.filename).suffix or suffix_fallback
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    path = UPLOAD_DIR / filename
    with path.open("wb") as destination:
        shutil.copyfileobj(upload.file, destination)
    print(f"[DEBUG] 文件已保存: {path} ({path.stat().st_size} bytes)")
    return path


def path_to_upload_url(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    return f"/uploads/{path.name}"


def build_reference_collage(image_paths: List[Path]) -> Path:
    valid_paths = [path for path in image_paths if path and path.exists()]
    if not valid_paths:
        raise HTTPException(status_code=400, detail="No valid reference images found.")
    if len(valid_paths) == 1:
        return valid_paths[0]

    cell_width = 768
    cell_height = 768
    cols = 2 if len(valid_paths) > 1 else 1
    rows = (len(valid_paths) + cols - 1) // cols
    canvas = Image.new(
        "RGB", (cols * cell_width, rows * cell_height), color=(245, 239, 228)
    )

    for index, path in enumerate(valid_paths):
        with Image.open(path) as img:
            tile = ImageOps.exif_transpose(img).convert("RGB")
            tile.thumbnail((cell_width - 24, cell_height - 24))
            cell = Image.new("RGB", (cell_width, cell_height), color=(255, 255, 255))
            offset_x = (cell_width - tile.width) // 2
            offset_y = (cell_height - tile.height) // 2
            cell.paste(tile, (offset_x, offset_y))
            x = (index % cols) * cell_width
            y = (index // cols) * cell_height
            canvas.paste(cell, (x, y))

    collage_name = (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_refs.png"
    )
    collage_path = UPLOAD_DIR / collage_name
    canvas.save(collage_path, format="PNG")
    return collage_path


def _extract_relay_error_text(response: requests.Response) -> str:
    body_preview = response.text[:800]
    try:
        payload = response.json()
    except ValueError:
        return body_preview

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

    return body_preview


def _raise_relay_http_error(response: requests.Response, operation: str) -> None:
    status = response.status_code
    upstream_message = _extract_relay_error_text(response)
    detail = (
        f"Relay {operation} API failed. "
        f"upstream_status={status}, upstream_message={upstream_message}"
    )
    # Upstream 4xx are request/auth/quota issues and should not be surfaced as 502.
    if 400 <= status < 500:
        raise HTTPException(status_code=400, detail=detail)
    raise HTTPException(status_code=502, detail=detail)


def persist_result(
    result: Dict[str, Any], session: requests.Session, verify_ssl: bool
) -> str:
    data = result.get("data") or []
    if not data:
        raise HTTPException(
            status_code=502, detail=f"Relay API returned no image data: {result}"
        )

    item = data[0]
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    output_path = OUTPUT_DIR / filename

    if item.get("b64_json"):
        output_path.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        image_response = session.get(
            item["url"], timeout=get_settings()["request_timeout"], verify=verify_ssl
        )
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)
    else:
        raise HTTPException(
            status_code=502, detail=f"Unsupported relay response payload: {result}"
        )

    return f"/outputs/{filename}"


def relay_generate_raw(
    prompt: str,
    model: str,
    size: str,
    api_key: str,
    base_url: str,
    verify_ssl: bool,
    use_env_proxy: bool,
    request_timeout: int,
) -> Dict[str, Any]:
    prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt
    print(f"[DEBUG] relay_generate_raw 请求: model={model}, size={size}")
    print(f"[DEBUG] relay_generate_raw prompt: {prompt_preview}")
    with build_session(use_env_proxy) as session:
        response = session.post(
            f"{base_url}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "prompt": prompt, "size": size},
            timeout=request_timeout,
            verify=verify_ssl,
        )
        if not response.ok:
            _raise_relay_http_error(response, operation="generate")
        result = response.json()
        return {"image_url": persist_result(result, session, verify_ssl)}


def relay_edit_raw(
    prompt: str,
    model: str,
    size: str,
    image_path: Path,
    mask_path: Optional[Path],
    api_key: str,
    base_url: str,
    verify_ssl: bool,
    use_env_proxy: bool,
    request_timeout: int,
) -> Dict[str, Any]:
    prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt
    print(
        f"[DEBUG] relay_edit_raw 请求: model={model}, size={size}, image={image_path.name}"
    )
    print(f"[DEBUG] relay_edit_raw prompt: {prompt_preview}")
    with build_session(use_env_proxy) as session:
        with image_path.open("rb") as image_file:
            files = {"image": (image_path.name, image_file, "image/png")}
            if mask_path:
                with mask_path.open("rb") as mask_file:
                    files["mask"] = (mask_path.name, mask_file, "image/png")
                    response = session.post(
                        f"{base_url}/images/edits",
                        headers={"Authorization": f"Bearer {api_key}"},
                        data={"model": model, "prompt": prompt, "size": size},
                        files=files,
                        timeout=request_timeout,
                        verify=verify_ssl,
                    )
            else:
                response = session.post(
                    f"{base_url}/images/edits",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"model": model, "prompt": prompt, "size": size},
                    files=files,
                    timeout=request_timeout,
                    verify=verify_ssl,
                )
        if not response.ok:
            _raise_relay_http_error(response, operation="edit")
        result = response.json()
        return {"image_url": persist_result(result, session, verify_ssl)}
