"""Generation and editing endpoints; blocking work uses FastAPI's thread pool."""

import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import resolve_runtime_settings
from ..errors import describe_error
from ..repository import ensure_project
from ..services.journal import (
    start_generation,
    complete_generation,
    fail_generation,
    update_source,
)
from ..services.images import (
    build_reference_collage,
    local_path_from_url,
    path_to_upload_url,
    relay_edit_raw,
    relay_generate_raw,
    save_upload_file,
)
from ..services.storyboard import (
    add_shot_to_project,
    build_composed_prompt,
    get_card_image_labels,
    get_card_image_paths,
    normalize_generation_args,
    parse_id_list,
)

router = APIRouter(prefix="/api", tags=["images"])


@router.post("/generate")
def generate_image(
    api_key: str = Form(""),
    base_url: str = Form(""),
    api_profile_id: str = Form(""),
    project_id: str = Form(""),
    project_name: str = Form(""),
    synopsis: Optional[str] = Form(None),
    continue_from_last: str = Form("false"),
    character_card_ids: str = Form(""),
    scene_card_ids: str = Form(""),
    prompt: str = Form(...),
    system_prompt: str = Form(""),
    model: str = Form("gpt-image-2"),
    size: str = Form("2048x1152"),
    reference_images: List[UploadFile] = File([]),
) -> Dict[str, Any]:
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    validated = normalize_generation_args(model, size)

    settings = resolve_runtime_settings(
        api_key=api_key, base_url=base_url, api_profile_id=api_profile_id
    )
    if not settings["api_key"]:
        raise HTTPException(status_code=400, detail="Missing API key.")

    project = ensure_project(
        project_id=project_id, project_name=project_name, synopsis=synopsis
    )
    selected_character_ids = parse_id_list(character_card_ids)
    selected_scene_ids = parse_id_list(scene_card_ids)
    use_last = continue_from_last.lower() == "true"
    last_shot = project["shots"][0] if project["shots"] else None

    request_id = uuid.uuid4().hex[:8]
    ref_count = len(reference_images) if reference_images else 0
    print(
        f"[DEBUG][{request_id}] generate_image 收到参数: project_id={project_id}, character_card_ids={selected_character_ids}, scene_card_ids={selected_scene_ids}, reference_images数量={ref_count}"
    )

    card_image_paths = get_card_image_paths(
        project, selected_character_ids, selected_scene_ids
    )
    card_image_labels = get_card_image_labels(
        project, selected_character_ids, selected_scene_ids
    )
    print(f"[DEBUG][{request_id}] 卡片图片路径: {[str(p) for p in card_image_paths]}")

    uploaded_paths: List[Path] = []
    if reference_images:
        for upload in reference_images:
            if upload and upload.filename:
                path = save_upload_file(upload)
                if path:
                    uploaded_paths.append(path)
    uploaded_urls = [path_to_upload_url(path) for path in uploaded_paths if path]
    print(f"[DEBUG][{request_id}] 上传参考图保存数量: {len(uploaded_paths)}")

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
    composed_prompt = build_composed_prompt(
        project,
        prompt.strip(),
        selected_character_ids,
        selected_scene_ids,
        has_reference_images=has_refs,
        system_prompt=system_prompt,
    )

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

    record_id = start_generation(
        project,
        mode="reference_generate"
        if has_refs
        else "continue"
        if use_last and last_shot
        else "generate",
        prompt=prompt.strip(),
        system_prompt=system_prompt.strip(),
        composed_prompt=composed_prompt,
        **validated,
        character_card_ids=selected_character_ids,
        scene_card_ids=selected_scene_ids,
        reference_images=reference_urls,
        source_image=last_shot["image_url"]
        if use_last and last_shot and not has_refs
        else None,
        timeout=settings["request_timeout"],
    )
    try:
        if reference_paths:
            print(
                f"[DEBUG][{request_id}] 使用 reference_generate 模式，参考图数量: {len(reference_paths)}"
            )
            collage_path = build_reference_collage(reference_paths)
            update_source(record_id, path_to_upload_url(collage_path))
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
            print(f"[DEBUG][{request_id}] 使用 continue 模式，基于上一镜")
            last_image_path = local_path_from_url(last_shot["image_url"])
            if not last_image_path:
                raise HTTPException(
                    status_code=400, detail="Last shot image not found locally."
                )
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
            print(f"[DEBUG][{request_id}] 使用 generate 模式（纯文生图，无参考图）")
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
    except Exception as exc:
        fail_generation(record_id, exc, settings["api_key"])
        info = describe_error(exc)
        logging.getLogger(__name__).warning(
            "Image generation failed: record=%s code=%s", record_id, info.code
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, info.message) from exc

    complete_generation(record_id, result["image_url"])
    print(
        f"[DEBUG][{request_id}] 生成完成, mode={mode}, composed_prompt前100字符: {composed_prompt[:100]}..."
    )
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
        system_prompt=system_prompt.strip(),
    )


@router.post("/edit")
def edit_image(
    api_key: str = Form(""),
    base_url: str = Form(""),
    api_profile_id: str = Form(""),
    project_id: str = Form(""),
    project_name: str = Form(""),
    synopsis: Optional[str] = Form(None),
    character_card_ids: str = Form(""),
    scene_card_ids: str = Form(""),
    prompt: str = Form(...),
    system_prompt: str = Form(""),
    model: str = Form("gpt-image-2"),
    size: str = Form("2048x1152"),
    image: Optional[UploadFile] = File(None),
    mask: Optional[UploadFile] = File(None),
    reference_images: List[UploadFile] = File([]),
) -> Dict[str, Any]:
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    validated = normalize_generation_args(model, size)

    settings = resolve_runtime_settings(
        api_key=api_key, base_url=base_url, api_profile_id=api_profile_id
    )
    if not settings["api_key"]:
        raise HTTPException(status_code=400, detail="Missing API key.")

    project = ensure_project(
        project_id=project_id, project_name=project_name, synopsis=synopsis
    )
    selected_character_ids = parse_id_list(character_card_ids)
    selected_scene_ids = parse_id_list(scene_card_ids)

    card_image_paths = get_card_image_paths(
        project, selected_character_ids, selected_scene_ids
    )
    card_image_labels = get_card_image_labels(
        project, selected_character_ids, selected_scene_ids
    )
    uploaded_paths: List[Path] = []
    if reference_images:
        for upload in reference_images:
            if upload and upload.filename:
                path = save_upload_file(upload)
                if path:
                    uploaded_paths.append(path)
    uploaded_urls = [path_to_upload_url(path) for path in uploaded_paths if path]
    reference_paths = card_image_paths + uploaded_paths

    has_refs = bool(reference_paths)
    composed_prompt = build_composed_prompt(
        project,
        prompt.strip(),
        selected_character_ids,
        selected_scene_ids,
        has_reference_images=has_refs,
        system_prompt=system_prompt,
    )

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
        main_image_path = save_upload_file(image)
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
        raise HTTPException(
            status_code=400,
            detail="Edit mode needs an uploaded image or an existing previous shot.",
        )

    mask_path: Optional[Path] = None
    mask_image_url: Optional[str] = None
    if mask and mask.filename:
        mask_path = save_upload_file(mask)
        mask_image_url = path_to_upload_url(mask_path)

    original_source_image_url = source_image_url
    if reference_paths and not mask_path and image_path:
        base_paths: List[Path] = []
        if image_path not in reference_paths:
            base_paths.append(image_path)
        base_paths.extend(reference_paths)
        image_path = build_reference_collage(base_paths)
        source_image_url = path_to_upload_url(image_path)

    record_id = start_generation(
        project,
        mode="edit",
        prompt=prompt.strip(),
        system_prompt=system_prompt.strip(),
        composed_prompt=composed_prompt,
        **validated,
        character_card_ids=selected_character_ids,
        scene_card_ids=selected_scene_ids,
        reference_images=reference_urls,
        source_image=source_image_url,
        original_source_image=original_source_image_url,
        mask_image=mask_image_url,
        timeout=settings["request_timeout"],
    )
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
    except Exception as exc:
        fail_generation(record_id, exc, settings["api_key"])
        info = describe_error(exc)
        logging.getLogger(__name__).warning(
            "Image edit failed: record=%s code=%s", record_id, info.code
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, info.message) from exc

    complete_generation(record_id, result["image_url"])
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
        system_prompt=system_prompt.strip(),
    )
