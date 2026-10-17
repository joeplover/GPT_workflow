"""Xiao Meng's planning and project orchestration primitives."""

from datetime import datetime, timezone
import os
import re
import uuid

import requests

from ..config import get_settings
from ..repository import mutate_project, now_iso
from .agent_settings import resolve_agent_settings

STAGES = ("brief", "script", "characters", "scenes", "storyboard", "generation")
STAGE_LABELS = {
    "brief": "创作目标",
    "script": "剧本规划",
    "characters": "人物设定",
    "scenes": "场景设定",
    "storyboard": "分镜规划",
    "generation": "分镜生图",
}


def empty_plan():
    return {
        "title": "",
        "brief": "",
        "tone": "",
        "screenplay": "",
        "characters": [],
        "scenes": [],
        "shots": [],
        "image_settings": {
            "model": get_settings()["default_model"],
            "size": get_settings()["default_size"],
        },
    }


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _title(text):
    cleaned = re.sub(r"\s+", " ", text.strip())
    cleaned = re.sub(r"^(我想|帮我|请帮我|我要|做一个|创作一个)\s*", "", cleaned)
    return (cleaned[:24] or "未命名故事").strip("，。！？ ")


def _extract_names(text, words):
    found = []
    for word in words:
        if word in text and word not in found:
            found.append(word)
    return found


def draft_plan(plan, user_text):
    """Create a useful first draft locally; an LLM can enrich the reply separately."""
    text = user_text.strip()
    result = {**empty_plan(), **plan}
    result["image_settings"] = {
        **empty_plan()["image_settings"],
        **plan.get("image_settings", {}),
    }
    if not result["title"]:
        result["title"] = _title(text)
    if not result["brief"] or any(key in text for key in ("改成", "换成", "调整", "重新")):
        result["brief"] = text
    if not result["tone"]:
        tone_words = _extract_names(
            text, ("电影感", "治愈", "悬疑", "赛博朋克", "国风", "写实", "浪漫", "黑色幽默")
        )
        result["tone"] = "、".join(tone_words) or "电影感、叙事清晰、画面统一"
    if not result["screenplay"]:
        result["screenplay"] = "\n".join(
            [
                "1. 建立世界与时间：用一个清晰的环境镜头交代故事气质。",
                "2. 人物出现：展示主角的状态与当前目标。",
                "3. 发生变化：一个事件打破原有的平衡。",
                "4. 做出选择：人物采取行动，情绪达到高点。",
                "5. 留下余韵：用具有记忆点的画面收束这一段故事。",
            ]
        )
    if not result["characters"]:
        result["characters"] = [
            {
                "name": "主角",
                "content": "故事核心人物，外形、年龄、服装和性格将在确认后继续细化；保持全片形象一致。",
            },
            {
                "name": "关键配角",
                "content": "推动主角做出选择的人物，与主角形成明确的关系和视觉区分。",
            },
        ]
    if not result["scenes"]:
        result["scenes"] = [
            {
                "name": "主要环境",
                "content": "服务故事情绪的主要场景，统一时间、天气、材质和色彩，作为分镜的视觉锚点。",
            },
            {
                "name": "转折空间",
                "content": "承载关键事件的场景，与主要环境保持世界观和光线连续。",
            },
        ]
    if not result["shots"]:
        seeds = [
            "建立镜头，交代环境、时间和整体氛围",
            "中景展示主角与环境的关系，人物状态清晰",
            "近景捕捉主角面对变化时的情绪",
            "动作镜头表现关键事件和人物选择",
            "收束镜头留下具有记忆点的画面",
        ]
        result["shots"] = [
            {"title": f"镜头 {i:02d}", "prompt": seed, "order": i}
            for i, seed in enumerate(seeds, 1)
        ]
    return result


def _chat_reply(text, plan):
    """Use an OpenAI-compatible chat endpoint when explicitly configured."""
    try:
        settings = resolve_agent_settings()
        if not settings["configured"] and os.getenv("PHOTODREAM_AGENT_LLM", "false").lower() != "true":
            return ""
        if not settings["api_key"]:
            return ""
        response = requests.post(
            settings["base_url"] + "/chat/completions",
            headers={"Authorization": f"Bearer {settings['api_key']}"},
            json={
                "model": settings["model"],
                "temperature": 0.7,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是 Photo Dream 的小梦 Agent。用简洁中文协助用户完成文案、剧本、角色、场景和分镜规划。只讨论创作，先给建议，再请用户确认当前阶段。",
                    },
                    {
                        "role": "user",
                        "content": f"当前规划：{plan}\n用户想法：{text}",
                    },
                ],
            },
            timeout=min(int(settings["request_timeout"]), 90),
        )
        response.raise_for_status()
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() if isinstance(content, str) else ""
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return ""


def respond(user_text, plan, stage, mode):
    plan = draft_plan(plan, user_text)
    generated = _chat_reply(user_text, plan)
    if generated:
        reply = generated
    else:
        label = STAGE_LABELS.get(stage, "创作规划")
        reply = (
            f"我先按“{plan['title']}”整理了一版{label}。\n\n"
            f"创作目标：{plan['brief']}\n"
            f"视觉基调：{plan['tone']}\n\n"
            "你可以直接告诉我需要修改的内容。确认当前阶段后，我会把结果写入项目。"
        )
    if mode == "autonomous":
        reply += "\n\n当前为完全托管模式，确认后我会连续推进后续阶段。"
    return plan, reply


def apply_stage(project_id, plan, stage):
    """Materialize a confirmed plan into the existing project aggregate."""
    def change(project):
        if stage in {"brief", "script", "characters", "scenes", "storyboard", "generation"}:
            project["name"] = plan.get("title") or project.get("name", "未命名项目")
            project["synopsis"] = plan.get("brief", "")
        if stage in {"script", "characters", "scenes", "storyboard", "generation"}:
            project["screenplay_text"] = plan.get("screenplay", "")
            project["shot_plan"] = plan.get("shots", [])
        if stage in {"characters", "scenes", "storyboard", "generation"}:
            for card_type, plan_key in (("character", "characters"), ("scene", "scenes")):
                key = f"{card_type}_cards"
                for draft in plan.get(plan_key, []):
                    name = draft.get("name", "").strip()
                    if not name:
                        continue
                    existing = next((c for c in project[key] if c["name"] == name), None)
                    if existing:
                        existing["content"] = draft.get("content", "").strip()
                    else:
                        project[key].append(
                            {
                                "id": uuid.uuid4().hex,
                                "name": name,
                                "content": draft.get("content", "").strip(),
                                "image_url": None,
                                "created_at": now_iso(),
                            }
                        )
        if stage in {"storyboard", "generation"}:
            chars = {c["name"]: c["id"] for c in project["character_cards"]}
            scenes = {c["name"]: c["id"] for c in project["scene_cards"]}
            character_ids = list(chars.values())
            scene_ids = list(scenes.values())
            existing = {f.get("title") for f in project["frames"]}
            settings = plan.get("image_settings", {})
            for draft in plan.get("shots", []):
                title = draft.get("title", "").strip() or f"镜头 {len(project['frames']) + 1:02d}"
                if title in existing:
                    continue
                project["frames"].append(
                    {
                        "id": uuid.uuid4().hex,
                        "title": title,
                        "prompt": draft.get("prompt", "").strip(),
                        "system_prompt": "保持角色、场景和整体视觉风格一致。",
                        "model": settings.get("model", get_settings()["default_model"]),
                        "size": settings.get("size", get_settings()["default_size"]),
                        "character_card_ids": character_ids,
                        "scene_card_ids": scene_ids,
                        "marked": True,
                        "status": "draft",
                        "image_url": None,
                        "error": "",
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                        "versions": [],
                    }
                )
            project["frames_initialized"] = True

    return mutate_project(project_id, change, f"agent.apply.{stage}")
