import base64
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUNTIME_DIR = BASE_DIR / "runtime"
OUTPUT_DIR = RUNTIME_DIR / "outputs"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
PROJECTS_FILE = RUNTIME_DIR / "projects.json"

for path in [RUNTIME_DIR, OUTPUT_DIR, UPLOAD_DIR]:
    path.mkdir(parents=True, exist_ok=True)

if not PROJECTS_FILE.exists():
    PROJECTS_FILE.write_text("[]", encoding="utf-8")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_projects() -> List[Dict[str, Any]]:
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_projects(items: List[Dict[str, Any]]) -> None:
    PROJECTS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def get_settings() -> Dict[str, Any]:
    request_timeout = int(os.getenv("HICODE_REQUEST_TIMEOUT", "300"))
    max_project_shots = int(os.getenv("HICODE_MAX_PROJECT_SHOTS", "200"))
    if request_timeout <= 0:
        request_timeout = 300
    if max_project_shots <= 0:
        max_project_shots = 200
    return {
        "api_key": os.getenv("HICODE_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
        "base_url": os.getenv("HICODE_BASE_URL", "https://api.hi-code.cc/v1").rstrip("/"),
        "default_model": os.getenv("HICODE_IMAGE_MODEL", "gpt-image-1"),
        "default_size": os.getenv("HICODE_IMAGE_SIZE", "1536x1024"),
        "verify_ssl": os.getenv("HICODE_VERIFY_SSL", "true").lower() != "false",
        "use_env_proxy": os.getenv("HICODE_USE_ENV_PROXY", "false").lower() == "true",
        "request_timeout": request_timeout,
        "max_project_shots": max_project_shots,
    }


def resolve_runtime_settings(api_key: Optional[str], base_url: Optional[str]) -> Dict[str, Any]:
    settings = get_settings()
    return {
        **settings,
        "api_key": (api_key or "").strip() or settings["api_key"],
        "base_url": (base_url or "").strip().rstrip("/") or settings["base_url"],
    }


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


async def save_upload_file(upload: UploadFile, suffix_fallback: str = ".png") -> Optional[Path]:
    if not upload or not upload.filename:
        return None
    ext = Path(upload.filename).suffix or suffix_fallback
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    path = UPLOAD_DIR / filename
    content = await upload.read()
    path.write_bytes(content)
    print(f"[DEBUG] 文件已保存: {path} ({len(content)} bytes)")
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
    canvas = Image.new("RGB", (cols * cell_width, rows * cell_height), color=(245, 239, 228))

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

    collage_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_refs.png"
    collage_path = UPLOAD_DIR / collage_name
    canvas.save(collage_path, format="PNG")
    return collage_path


def normalize_project(project: Dict[str, Any]) -> Dict[str, Any]:
    project.setdefault("shots", [])
    project.setdefault("shot_plan", [])
    project.setdefault("character_cards", [])
    project.setdefault("scene_cards", [])
    project.setdefault("synopsis", "")
    project.setdefault("screenplay_text", "")
    project.setdefault("created_at", now_iso())
    project.setdefault("updated_at", now_iso())
    return project


def ensure_project(project_id: str, project_name: str, synopsis: str) -> Dict[str, Any]:
    projects = load_projects()
    for project in projects:
        if project["id"] == project_id:
            normalize_project(project)
            if project_name.strip():
                project["name"] = project_name.strip()
            if synopsis is not None:
                project["synopsis"] = synopsis.strip()
            project["updated_at"] = now_iso()
            save_projects(projects)
            return project

    if not project_id:
        project_id = uuid.uuid4().hex

    project = normalize_project(
        {
            "id": project_id,
            "name": project_name.strip() or f"Project {len(projects) + 1}",
            "synopsis": synopsis.strip(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    projects.insert(0, project)
    save_projects(projects)
    return project


def get_project(project_id: str) -> Dict[str, Any]:
    projects = load_projects()
    for project in projects:
        if project["id"] == project_id:
            return normalize_project(project)
    raise HTTPException(status_code=404, detail="Project not found.")


def update_project(project: Dict[str, Any]) -> None:
    projects = load_projects()
    for index, item in enumerate(projects):
        if item["id"] == project["id"]:
            project["updated_at"] = now_iso()
            projects[index] = normalize_project(project)
            save_projects(projects)
            return
    raise HTTPException(status_code=404, detail="Project not found while saving.")


def get_card_text(project: Dict[str, Any], card_type: str, selected_ids: List[str]) -> List[str]:
    key = "character_cards" if card_type == "character" else "scene_cards"
    cards = project.get(key, [])
    selected = []
    id_set = {item for item in selected_ids if item}
    for card in cards:
        if card["id"] in id_set:
            selected.append(f'{card["name"]}: {card["content"]}')
    return selected


def get_card_image_paths(
    project: Dict[str, Any],
    selected_character_ids: List[str],
    selected_scene_ids: List[str],
) -> List[Path]:
    paths: List[Path] = []
    char_id_set = {item for item in selected_character_ids if item}
    scene_id_set = {item for item in selected_scene_ids if item}
    for card in project.get("character_cards", []):
        if card["id"] in char_id_set:
            image_url = card.get("image_url")
            if image_url:
                p = local_path_from_url(image_url)
                if p:
                    paths.append(p)
                else:
                    print(f"[WARN] 角色卡 '{card['name']}' 的图片文件不存在: {image_url}")
            else:
                print(f"[WARN] 角色卡 '{card['name']}' 没有上传图片")
    for card in project.get("scene_cards", []):
        if card["id"] in scene_id_set:
            image_url = card.get("image_url")
            if image_url:
                p = local_path_from_url(image_url)
                if p:
                    paths.append(p)
                else:
                    print(f"[WARN] 场景卡 '{card['name']}' 的图片文件不存在: {image_url}")
            else:
                print(f"[WARN] 场景卡 '{card['name']}' 没有上传图片")
    return paths


def get_card_image_labels(
    project: Dict[str, Any],
    selected_character_ids: List[str],
    selected_scene_ids: List[str],
) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    char_id_set = {item for item in selected_character_ids if item}
    scene_id_set = {item for item in selected_scene_ids if item}
    for card in project.get("character_cards", []):
        if card["id"] in char_id_set and card.get("image_url"):
            labels[card["image_url"]] = f"来自角色卡: {card['name']}"
    for card in project.get("scene_cards", []):
        if card["id"] in scene_id_set and card.get("image_url"):
            labels[card["image_url"]] = f"来自场景卡: {card['name']}"
    return labels


def parse_screenplay_to_plan(screenplay_text: str) -> List[Dict[str, Any]]:
    lines = [line.strip() for line in screenplay_text.splitlines() if line.strip()]
    shots: List[Dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        cleaned = line
        if ". " in line[:8]:
            prefix, rest = line.split(". ", 1)
            if prefix.isdigit():
                cleaned = rest.strip()
        cleaned = re.sub(r"^\s*\d+\s*[、.．)\-]\s*", "", cleaned).strip()
        shot = {
            "id": uuid.uuid4().hex,
            "order": index,
            "title": f"Beat {index}",
            "prompt_seed": cleaned,
            "created_at": now_iso(),
        }
        shots.append(shot)
    return shots


def parse_id_list(raw_value: str) -> List[str]:
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def build_composed_prompt(project: Dict[str, Any], prompt: str, character_ids: List[str], scene_ids: List[str], has_reference_images: bool = False) -> str:
    parts: List[str] = []

    if has_reference_images:
        parts.append("[重要指令] 请严格保持参考图中所有角色的外貌、服装、面部特征完全一致。将角色自然地融入到当前镜头描述的场景中。不要改变角色的样貌。")

    if project.get("synopsis", "").strip():
        parts.append(f"Project setup: {project['synopsis'].strip()}")

    character_lines = get_card_text(project, "character", character_ids)
    if character_lines:
        parts.append("Character cards: " + " | ".join(character_lines))

    scene_lines = get_card_text(project, "scene", scene_ids)
    if scene_lines:
        parts.append("Scene cards: " + " | ".join(scene_lines))

    parts.append("Current shot: " + prompt.strip())
    return "\n".join(parts)


def normalize_generation_args(model: str, size: str) -> Dict[str, str]:
    model_clean = model.strip()
    size_clean = size.strip()
    if not model_clean:
        raise HTTPException(status_code=400, detail="Model is required.")
    if not re.match(r"^\d{3,5}x\d{3,5}$", size_clean):
        raise HTTPException(status_code=400, detail="Size must be formatted as WIDTHxHEIGHT, for example 1536x1024.")
    return {"model": model_clean, "size": size_clean}


def persist_result(result: Dict[str, Any], session: requests.Session, verify_ssl: bool) -> str:
    data = result.get("data") or []
    if not data:
        raise HTTPException(status_code=502, detail=f"Relay API returned no image data: {result}")

    item = data[0]
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    output_path = OUTPUT_DIR / filename

    if item.get("b64_json"):
        output_path.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        image_response = session.get(item["url"], timeout=300, verify=verify_ssl)
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)
    else:
        raise HTTPException(status_code=502, detail=f"Unsupported relay response payload: {result}")

    return f"/outputs/{filename}"


def relay_generate_raw(prompt: str, model: str, size: str, api_key: str, base_url: str, verify_ssl: bool, use_env_proxy: bool, request_timeout: int) -> Dict[str, Any]:
    prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt
    print(f"[DEBUG] relay_generate_raw 请求: model={model}, size={size}")
    print(f"[DEBUG] relay_generate_raw prompt: {prompt_preview}")
    session = build_session(use_env_proxy)
    response = session.post(
        f"{base_url}/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "prompt": prompt, "size": size},
        timeout=request_timeout,
        verify=verify_ssl,
    )
    if not response.ok:
        body_preview = response.text[:300]
        raise HTTPException(status_code=502, detail=f"Relay generate API failed: HTTP {response.status_code}, body={body_preview}")
    result = response.json()
    return {"image_url": persist_result(result, session, verify_ssl)}


def relay_edit_raw(prompt: str, model: str, size: str, image_path: Path, mask_path: Optional[Path], api_key: str, base_url: str, verify_ssl: bool, use_env_proxy: bool, request_timeout: int) -> Dict[str, Any]:
    prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt
    print(f"[DEBUG] relay_edit_raw 请求: model={model}, size={size}, image={image_path.name}")
    print(f"[DEBUG] relay_edit_raw prompt: {prompt_preview}")
    session = build_session(use_env_proxy)
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
        body_preview = response.text[:300]
        raise HTTPException(status_code=502, detail=f"Relay edit API failed: HTTP {response.status_code}, body={body_preview}")
    result = response.json()
    return {"image_url": persist_result(result, session, verify_ssl)}


def add_shot_to_project(
    project_id: str,
    mode: str,
    prompt: str,
    composed_prompt: str,
    model: str,
    size: str,
    image_url: str,
    source_image: Optional[str],
    mask_image: Optional[str],
    continue_from_last: bool,
    character_card_ids: List[str],
    scene_card_ids: List[str],
    reference_images: List[str],
    reference_sources: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    project = get_project(project_id)
    shot_number = len(project["shots"]) + 1
    shot = {
        "id": uuid.uuid4().hex,
        "shot_number": shot_number,
        "mode": mode,
        "prompt": prompt,
        "composed_prompt": composed_prompt,
        "model": model,
        "size": size,
        "image_url": image_url,
        "source_image": source_image,
        "mask_image": mask_image,
        "continue_from_last": continue_from_last,
        "character_card_ids": character_card_ids,
        "scene_card_ids": scene_card_ids,
        "reference_images": reference_images,
        "reference_sources": reference_sources or {},
        "created_at": now_iso(),
    }
    project["shots"].insert(0, shot)
    settings = get_settings()
    max_project_shots = settings["max_project_shots"]
    if len(project["shots"]) > max_project_shots:
        project["shots"] = project["shots"][:max_project_shots]
    for index, item in enumerate(project["shots"], start=1):
        item["shot_number"] = index
    update_project(project)
    return {"project": project, "shot": shot}


app = FastAPI(title="HiCode Image Studio", description="Relay-API-based storyboard image platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def debug_request_middleware(request, call_next):
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        print(f"[MIDDLEWARE] {request.method} {request.url.path} | content-type={content_type}")
    response = await call_next(request)
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def read_config() -> Dict[str, Any]:
    settings = get_settings()
    return {
        "base_url": settings["base_url"],
        "default_model": settings["default_model"],
        "default_size": settings["default_size"],
        "verify_ssl": settings["verify_ssl"],
        "use_env_proxy": settings["use_env_proxy"],
        "request_timeout": settings["request_timeout"],
        "max_project_shots": settings["max_project_shots"],
        "api_key": settings["api_key"],
        "has_api_key": bool(settings["api_key"]),
        "supported_modes": ["generate", "edit"],
        "suggested_models": ["gpt-image-1", "gpt-image-2"],
        "suggested_sizes": ["1024x1024", "1536x1024", "1024x1536"],
    }


@app.get("/api/debug/state")
def debug_state(project_id: str = "") -> Dict[str, Any]:
    """诊断端点：查看当前项目卡片、上传文件等状态"""
    upload_files = sorted(UPLOAD_DIR.glob("*")) if UPLOAD_DIR.exists() else []
    output_files = sorted(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else []
    project_info = None
    if project_id:
        try:
            project = get_project(project_id)
            project_info = {
                "name": project.get("name"),
                "character_cards_count": len(project.get("character_cards", [])),
                "scene_cards_count": len(project.get("scene_cards", [])),
                "shots_count": len(project.get("shots", [])),
                "character_cards": [
                    {"name": c["name"], "has_image": bool(c.get("image_url")), "image_url": c.get("image_url")}
                    for c in project.get("character_cards", [])
                ],
                "scene_cards": [
                    {"name": c["name"], "has_image": bool(c.get("image_url")), "image_url": c.get("image_url")}
                    for c in project.get("scene_cards", [])
                ],
            }
        except Exception:
            pass
    return {
        "upload_dir": str(UPLOAD_DIR),
        "upload_files": [f.name for f in upload_files],
        "upload_file_sizes": {f.name: f.stat().st_size for f in upload_files},
        "output_files": [f.name for f in output_files],
        "project_id": project_id,
        "project": project_info,
    }


@app.get("/api/projects")
def read_projects() -> List[Dict[str, Any]]:
    return [normalize_project(item) for item in load_projects()]


@app.get("/api/projects/{project_id}")
def read_project(project_id: str) -> Dict[str, Any]:
    return get_project(project_id)


@app.post("/api/projects")
def create_project(name: str = Form(...), synopsis: str = Form("")) -> Dict[str, Any]:
    return ensure_project(project_id="", project_name=name, synopsis=synopsis)


@app.post("/api/projects/{project_id}/cards")
async def create_card(
    project_id: str,
    card_type: str = Form(...),
    name: str = Form(...),
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    if card_type not in {"character", "scene"}:
        raise HTTPException(status_code=400, detail="card_type must be character or scene.")
    project = get_project(project_id)
    key = "character_cards" if card_type == "character" else "scene_cards"

    image_url: Optional[str] = None
    if image and image.filename:
        saved_path = await save_upload_file(image)
        if saved_path:
            image_url = path_to_upload_url(saved_path)

    project[key].append(
        {
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "content": content.strip(),
            "image_url": image_url,
            "created_at": now_iso(),
        }
    )
    update_project(project)
    return project


@app.post("/api/projects/{project_id}/screenplay")
def parse_screenplay(
    project_id: str,
    screenplay_text: str = Form(...),
) -> Dict[str, Any]:
    project = get_project(project_id)
    project["screenplay_text"] = screenplay_text.strip()
    project["shot_plan"] = parse_screenplay_to_plan(screenplay_text)
    update_project(project)
    return project


@app.post("/api/generate")
async def generate_image(
    api_key: str = Form(""),
    base_url: str = Form(""),
    project_id: str = Form(""),
    project_name: str = Form(""),
    synopsis: str = Form(""),
    continue_from_last: str = Form("false"),
    character_card_ids: str = Form(""),
    scene_card_ids: str = Form(""),
    prompt: str = Form(...),
    model: str = Form("gpt-image-1"),
    size: str = Form("1536x1024"),
    reference_images: List[UploadFile] = File(default=None),
) -> Dict[str, Any]:
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    validated = normalize_generation_args(model, size)

    settings = resolve_runtime_settings(api_key=api_key, base_url=base_url)
    if not settings["api_key"]:
        raise HTTPException(status_code=400, detail="Missing API key.")

    project = ensure_project(project_id=project_id, project_name=project_name, synopsis=synopsis)
    selected_character_ids = parse_id_list(character_card_ids)
    selected_scene_ids = parse_id_list(scene_card_ids)
    use_last = continue_from_last.lower() == "true"
    last_shot = project["shots"][0] if project["shots"] else None

    ref_count = len(reference_images) if reference_images else 0
    print(f"[DEBUG] generate_image 收到参数: project_id={project_id}, character_card_ids={selected_character_ids}, scene_card_ids={selected_scene_ids}, reference_images数量={ref_count}")

    card_image_paths = get_card_image_paths(project, selected_character_ids, selected_scene_ids)
    card_image_labels = get_card_image_labels(project, selected_character_ids, selected_scene_ids)
    print(f"[DEBUG] 卡片图片路径: {[str(p) for p in card_image_paths]}")

    uploaded_paths: List[Path] = []
    if reference_images:
        for upload in reference_images:
            if upload and upload.filename:
                path = await save_upload_file(upload)
                if path:
                    uploaded_paths.append(path)
    uploaded_urls = [path_to_upload_url(path) for path in uploaded_paths if path]
    print(f"[DEBUG] 上传参考图保存数量: {len(uploaded_paths)}")

    reference_paths = card_image_paths + uploaded_paths
    # 去重（基于文件绝对路径）
    seen = set()
    deduped_paths: List[Path] = []
    for p in reference_paths:
        abs_path = p.resolve()
        if abs_path not in seen:
            seen.add(abs_path)
            deduped_paths.append(p)
    reference_paths = deduped_paths

    has_refs = bool(reference_paths)
    composed_prompt = build_composed_prompt(project, prompt.strip(), selected_character_ids, selected_scene_ids, has_reference_images=has_refs)

    reference_urls: List[str] = []
    reference_sources: Dict[str, str] = {}
    for p in card_image_paths:
        url = path_to_upload_url(p)
        if url:
            reference_urls.append(url)
            reference_sources[url] = card_image_labels.get(url, "来自卡片参考图")
    for url in uploaded_urls:
        reference_urls.append(url)
        reference_sources[url] = "单独参考图"

    try:
        if reference_paths:
            print(f"[DEBUG] 使用 reference_generate 模式，参考图数量: {len(reference_paths)}")
            collage_path = build_reference_collage(reference_paths)
            result = relay_edit_raw(
                prompt=composed_prompt,
                model=validated["model"],
                size=validated["size"],
                image_path=collage_path,
                mask_path=None,
                api_key=settings["api_key"],
                base_url=settings["base_url"],
                verify_ssl=settings["verify_ssl"],
                use_env_proxy=settings["use_env_proxy"],
                request_timeout=settings["request_timeout"],
            )
            source_image = path_to_upload_url(collage_path)
            mode = "reference_generate"
        elif use_last and last_shot:
            print(f"[DEBUG] 使用 continue 模式，基于上一镜")
            last_image_path = local_path_from_url(last_shot["image_url"])
            if not last_image_path:
                raise HTTPException(status_code=400, detail="Last shot image not found locally.")
            result = relay_edit_raw(
                prompt=composed_prompt,
                model=validated["model"],
                size=validated["size"],
                image_path=last_image_path,
                mask_path=None,
                api_key=settings["api_key"],
                base_url=settings["base_url"],
                verify_ssl=settings["verify_ssl"],
                use_env_proxy=settings["use_env_proxy"],
                request_timeout=settings["request_timeout"],
            )
            source_image = last_shot["image_url"]
            mode = "continue"
        else:
            print(f"[DEBUG] 使用 generate 模式（纯文生图，无参考图）")
            result = relay_generate_raw(
                prompt=composed_prompt,
                model=validated["model"],
                size=validated["size"],
                api_key=settings["api_key"],
                base_url=settings["base_url"],
                verify_ssl=settings["verify_ssl"],
                use_env_proxy=settings["use_env_proxy"],
                request_timeout=settings["request_timeout"],
            )
            source_image = None
            mode = "generate"
    except requests.exceptions.SSLError as exc:
        raise HTTPException(status_code=502, detail=f"SSL error while calling relay API: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Relay API request failed: {exc}") from exc

    print(f"[DEBUG] 生成完成, mode={mode}, composed_prompt前100字符: {composed_prompt[:100]}...")
    return add_shot_to_project(
        project_id=project["id"],
        mode=mode,
        prompt=prompt.strip(),
        composed_prompt=composed_prompt,
        model=validated["model"],
        size=validated["size"],
        image_url=result["image_url"],
        source_image=source_image,
        mask_image=None,
        continue_from_last=use_last,
        character_card_ids=selected_character_ids,
        scene_card_ids=selected_scene_ids,
        reference_images=[url for url in reference_urls if url],
        reference_sources=reference_sources,
    )


@app.post("/api/edit")
async def edit_image(
    api_key: str = Form(""),
    base_url: str = Form(""),
    project_id: str = Form(""),
    project_name: str = Form(""),
    synopsis: str = Form(""),
    character_card_ids: str = Form(""),
    scene_card_ids: str = Form(""),
    prompt: str = Form(...),
    model: str = Form("gpt-image-1"),
    size: str = Form("1536x1024"),
    image: Optional[UploadFile] = File(None),
    mask: Optional[UploadFile] = File(None),
    reference_images: List[UploadFile] = File([]),
) -> Dict[str, Any]:
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    validated = normalize_generation_args(model, size)

    settings = resolve_runtime_settings(api_key=api_key, base_url=base_url)
    if not settings["api_key"]:
        raise HTTPException(status_code=400, detail="Missing API key.")

    project = ensure_project(project_id=project_id, project_name=project_name, synopsis=synopsis)
    selected_character_ids = parse_id_list(character_card_ids)
    selected_scene_ids = parse_id_list(scene_card_ids)

    card_image_paths = get_card_image_paths(project, selected_character_ids, selected_scene_ids)
    card_image_labels = get_card_image_labels(project, selected_character_ids, selected_scene_ids)
    uploaded_paths: List[Path] = []
    if reference_images:
        for upload in reference_images:
            if upload and upload.filename:
                path = await save_upload_file(upload)
                if path:
                    uploaded_paths.append(path)
    uploaded_urls = [path_to_upload_url(path) for path in uploaded_paths if path]
    reference_paths = card_image_paths + uploaded_paths

    has_refs = bool(reference_paths)
    composed_prompt = build_composed_prompt(project, prompt.strip(), selected_character_ids, selected_scene_ids, has_reference_images=has_refs)

    reference_urls: List[str] = []
    reference_sources: Dict[str, str] = {}
    for p in card_image_paths:
        url = path_to_upload_url(p)
        if url:
            reference_urls.append(url)
            reference_sources[url] = card_image_labels.get(url, "来自卡片参考图")
    for url in uploaded_urls:
        reference_urls.append(url)
        reference_sources[url] = "单独参考图"

    image_path: Optional[Path] = None
    source_image_url: Optional[str] = None
    if image and image.filename:
        main_image_path = await save_upload_file(image)
        image_path = main_image_path
        source_image_url = path_to_upload_url(main_image_path)
    elif project["shots"]:
        source_image_url = project["shots"][0]["image_url"]
        image_path = local_path_from_url(source_image_url)
    elif reference_paths:
        collage_path = build_reference_collage(reference_paths)
        image_path = collage_path
        source_image_url = path_to_upload_url(collage_path)

    if not image_path:
        raise HTTPException(status_code=400, detail="Edit mode needs an uploaded image or an existing previous shot.")

    mask_path: Optional[Path] = None
    mask_image_url: Optional[str] = None
    if mask and mask.filename:
        mask_path = await save_upload_file(mask)
        mask_image_url = path_to_upload_url(mask_path)

    if reference_paths and not mask_path and image_path:
        base_paths: List[Path] = []
        if image_path not in reference_paths:
            base_paths.append(image_path)
        base_paths.extend(reference_paths)
        image_path = build_reference_collage(base_paths)
        source_image_url = path_to_upload_url(image_path)

    try:
        result = relay_edit_raw(
            prompt=composed_prompt,
            model=validated["model"],
            size=validated["size"],
            image_path=image_path,
            mask_path=mask_path,
            api_key=settings["api_key"],
            base_url=settings["base_url"],
            verify_ssl=settings["verify_ssl"],
            use_env_proxy=settings["use_env_proxy"],
            request_timeout=settings["request_timeout"],
        )
    except requests.exceptions.SSLError as exc:
        raise HTTPException(status_code=502, detail=f"SSL error while calling relay edit API: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Relay edit API request failed: {exc}") from exc

    return add_shot_to_project(
        project_id=project["id"],
        mode="edit",
        prompt=prompt.strip(),
        composed_prompt=composed_prompt,
        model=validated["model"],
        size=validated["size"],
        image_url=result["image_url"],
        source_image=source_image_url,
        mask_image=mask_image_url,
        continue_from_last=not bool(image and image.filename),
        character_card_ids=selected_character_ids,
        scene_card_ids=selected_scene_ids,
        reference_images=[url for url in reference_urls if url],
        reference_sources=reference_sources,
    )
