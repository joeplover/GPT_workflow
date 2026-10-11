"""Storyboard prompts, screenplay parsing and atomic shot history updates."""

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ..config import get_settings
from ..repository import mutate_project, now_iso
from .images import local_path_from_url


def get_card_text(
    project: Dict[str, Any], card_type: str, selected_ids: List[str]
) -> List[str]:
    key = "character_cards" if card_type == "character" else "scene_cards"
    cards = project.get(key, [])
    selected = []
    id_set = {item for item in selected_ids if item}
    for card in cards:
        if card["id"] in id_set:
            selected.append(f"{card['name']}: {card['content']}")
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
                    print(
                        f"[WARN] 角色卡 '{card['name']}' 的图片文件不存在: {image_url}"
                    )
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
                    print(
                        f"[WARN] 场景卡 '{card['name']}' 的图片文件不存在: {image_url}"
                    )
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


def build_composed_prompt(
    project: Dict[str, Any],
    prompt: str,
    character_ids: List[str],
    scene_ids: List[str],
    has_reference_images: bool = False,
    system_prompt: str = "",
) -> str:
    parts: List[str] = []

    if system_prompt.strip():
        parts.append("System instructions: " + system_prompt.strip())

    if has_reference_images:
        parts.append(
            "[重要指令] 请严格保持参考图中所有角色的外貌、服装、面部特征完全一致。将角色自然地融入到当前镜头描述的场景中。不要改变角色的样貌。"
        )

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
    size_clean = re.sub(r"\s+", "", size).lower().replace("×", "x").replace("*", "x")
    if not model_clean:
        raise HTTPException(status_code=400, detail="Model is required.")
    if not re.fullmatch(r"[1-9]\d{1,4}x[1-9]\d{1,4}", size_clean):
        raise HTTPException(
            status_code=400,
            detail="Size must be formatted as WIDTHxHEIGHT, for example 2048x1152.",
        )
    return {"model": model_clean, "size": size_clean}


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
    system_prompt: str = "",
) -> Dict[str, Any]:
    shot = {
        "id": uuid.uuid4().hex,
        "shot_number": 1,
        "mode": mode,
        "prompt": prompt,
        "system_prompt": system_prompt,
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

    def append_shot(project: Dict[str, Any]) -> None:
        project["shots"].insert(0, shot)
        max_project_shots = get_settings()["max_project_shots"]
        project["shots"] = project["shots"][:max_project_shots]
        for index, item in enumerate(project["shots"], start=1):
            item["shot_number"] = index

    project = mutate_project(project_id, append_shot, "image.generate.complete")
    return {"project": project, "shot": shot}
