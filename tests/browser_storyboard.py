"""Optional UI smoke test: run against a server, with mocked image API responses.

pip install playwright
python tests/browser_storyboard.py --url http://127.0.0.1:8090 --chrome /usr/bin/google-chrome
"""

import argparse
import asyncio
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def check(args):
    project = {
        "id": "demo",
        "name": "雨夜来信",
        "character_cards": [],
        "scene_cards": [],
    }
    frames = []
    active = 0
    maximum = 0
    calls = 0
    fail_once = True
    errors = []
    next_frame_number = 0
    project_available = True
    fail_frame_saves = False
    generation_gate = None

    def make_frame(body):
        nonlocal next_frame_number
        next_frame_number += 1
        frame = {
            "prompt": "",
            "system_prompt": "",
            "model": "gpt-image-2",
            "size": "2048x1152",
            **body,
            "id": str(next_frame_number),
            "title": f"镜头 {len(frames) + 1:02d}",
            "status": "draft",
            "image_url": None,
            "versions": [],
            "error": "",
        }
        frames.append(frame)
        return frame

    async def mock(route):
        nonlocal active, maximum, calls, fail_once, project_available
        request = route.request
        path = urlparse(request.url).path
        body = (
            request.post_data_json
            if request.method == "POST"
            and request.headers.get("content-type") == "application/json"
            else {}
        )
        if path == "/api/auth/me":
            data = {
                "id": "demo-user",
                "username": "demo",
                "display_name": "测试用户",
                "is_admin": False,
            }
        elif path == "/api/config":
            data = {
                "base_url": "http://test-relay/v1",
                "has_api_key": True,
                "default_model": "gpt-image-2",
                "default_size": "2048x1152",
                "suggested_models": ["gpt-image-2"],
                "suggested_sizes": ["2048x1152", "1024x1024"],
                "size_presets": [
                    {"size": "2048x1152", "label": "16:9 · 2048 × 1152"},
                    {"size": "1024x1024", "label": "1:1 · 1024 × 1024"},
                    {"size": "1920x1080", "label": "16:9 · 1920 × 1080"},
                    {"size": "1080x1920", "label": "9:16 · 1080 × 1920"},
                ],
            }
        elif path == "/api/projects":
            if request.method == "POST":
                envelope = BytesParser(policy=default).parsebytes(
                    (
                        "Content-Type: " + request.headers["content-type"] + "\r\n\r\n"
                    ).encode()
                    + request.post_data_buffer
                )
                fields = {
                    part.get_param(
                        "name", header="content-disposition"
                    ): part.get_payload(decode=True).decode()
                    for part in envelope.iter_parts()
                }
                project["name"] = fields["name"]
                project_available = True
                data = deepcopy(project)
            else:
                data = [project] if project_available else []
        elif path == "/api/projects/demo":
            data = {**project, "frames": deepcopy(frames)}
        elif path == "/api/projects/demo/workspace":
            project.setdefault("workspace", {}).update(body)
            data = deepcopy(project["workspace"])
        elif path == "/api/projects/demo/events":
            data = [
                {
                    "id": 1,
                    "operation": "frame.save",
                    "created_at": "2026-10-11T00:00:00+00:00",
                    "changes": [],
                }
            ]
        elif path == "/api/projects/demo/frames/initialize":
            if not project.get("frames_initialized"):
                if not frames:
                    make_frame({})
                project["frames_initialized"] = True
            data = deepcopy(frames)
        elif path == "/api/projects/demo/cards":
            envelope = BytesParser(policy=default).parsebytes(
                (
                    "Content-Type: " + request.headers["content-type"] + "\r\n\r\n"
                ).encode()
                + request.post_data_buffer
            )
            fields = {}
            has_image = False
            for part in envelope.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "image":
                    has_image = True
                else:
                    fields[name] = part.get_payload(decode=True).decode()
            card_type = fields["card_type"]
            cards = project[f"{card_type}_cards"]
            cards.append(
                {
                    "id": f"{card_type}-{len(cards) + 1}",
                    "name": fields["name"],
                    "content": fields["content"],
                    "image_url": "/outputs/demo-1-card.svg" if has_image else None,
                }
            )
            data = deepcopy(project)
        elif path == "/api/projects/demo/frames/apply-cards":
            data = []
            for frame in frames:
                if frame["id"] in body["frame_ids"]:
                    frame["character_card_ids"] = body["character_card_ids"]
                    frame["scene_card_ids"] = body["scene_card_ids"]
                    data.append(deepcopy(frame))
        elif path == "/api/projects/demo/frames":
            if request.method == "POST":
                data = make_frame(body)
            else:
                data = deepcopy(frames)
        else:
            frame = next(f for f in frames if f["id"] == path.split("/")[5])
            if request.method == "DELETE":
                frames.remove(frame)
                project["frames_initialized"] = True
                await route.fulfill(json=deepcopy(frames))
                return
            if path.endswith("/generate"):
                frame.update(
                    {k: v for k, v in body.items() if k not in ("api_key", "base_url")}
                )
                frame["status"] = "generating"
                active += 1
                calls += 1
                maximum = max(maximum, active)
                try:
                    if generation_gate is None:
                        await asyncio.sleep(0.6)
                    else:
                        await generation_gate.wait()
                    if frame["id"] == "2" and fail_once:
                        fail_once = False
                        frame.update(status="failed", error="测试上游失败，可重试")
                        await route.fulfill(status=502, json={"detail": frame["error"]})
                        return
                    image_url = (
                        f"/outputs/demo-{frame['id']}-{len(frame['versions'])}.svg"
                    )
                    frame["versions"].insert(
                        0, {"image_url": image_url, "prompt": frame["prompt"]}
                    )
                    frame.update(
                        status="ready",
                        image_url=image_url,
                        generated_prompt=frame["prompt"],
                        error="",
                    )
                finally:
                    active -= 1
            else:
                if fail_frame_saves:
                    await route.fulfill(status=503, json={"detail": "模拟保存断网"})
                    return
                frame.update(body)
            data = deepcopy(frame)
        await route.fulfill(json=data)

    async def image(route):
        # Test-only SVG scenes, never returned by the production service.
        ident = int(urlparse(route.request.url).path.split("-")[1])
        hue = ["#7d9697", "#ad7e5d", "#7e8969", "#6f7b91", "#97877c"][(ident - 1) % 5]
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675">
        <defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="{hue}"/><stop offset="1" stop-color="#ddcab1"/></linearGradient></defs>
        <rect width="1200" height="675" fill="url(#sky)"/><circle cx="840" cy="210" r="54" fill="#f5dfb5"/>
        <path d="M0 410 Q240 240 450 390 T900 370 L1200 460V675H0Z" fill="#465b55"/>
        <path d="M0 520 Q300 380 580 510 T1200 450V675H0Z" fill="#263d3c"/>
        <path d="M620 440L400 675H800L670 440Z" fill="#bcac8b"/>
        <rect x="624" y="389" width="13" height="61" fill="#232f2b"/><circle cx="630" cy="382" r="8" fill="#232f2b"/>
        </svg>'''
        await route.fulfill(content_type="image/svg+xml", body=svg)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=args.chrome, args=["--no-sandbox"]
        )
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        if getattr(args, "session_cookie", None):
            await page.context.add_cookies(
                [
                    {
                        "name": "workflow_session",
                        "value": args.session_cookie,
                        "url": args.url,
                    }
                ]
            )
        await page.route("**/api/**", mock)
        await page.route("**/outputs/**", image)
        await page.goto(args.url + "/storyboard")
        await expect(page.locator("#connection")).to_have_text("已连接")
        await page.locator("#operationPanel summary").click()
        await expect(page.locator("#operationList")).to_contain_text("保存镜头修改")
        await page.locator("#operationPanel summary").click()
        await expect(page.locator(".viewer #filmScroll")).to_have_count(1)
        await expect(page.locator("#previewImage")).to_have_count(0)
        await expect(page.locator(".timeline")).to_have_count(1)
        await expect(page.locator(".film-frame")).to_have_count(1)
        first_id = frames[0]["id"]
        await page.reload()
        await expect(page.locator(".film-frame")).to_have_count(1)
        assert frames[0]["id"] == first_id
        prompts = [
            "雨夜，远景，汽车驶入城市",
            "车内特写，人物凝望窗外",
            "中景，人物推开咖啡馆的门",
            "桌面特写，一封未拆开的信",
            "近景，人物抬头看向门口",
        ]
        for index, prompt in enumerate(prompts):
            if index:
                await page.locator("#filmCreate").click()
            await expect(page.locator(".film-frame")).to_have_count(index + 1)
            await expect(page.locator("#prompt")).to_have_value("")
            await expect(page.locator("#selectedPosition")).to_have_text(
                f"{index + 1:02d} / {index + 1:02d}"
            )
            assert frames[index]["prompt"] == ""
            assert not await page.locator("dialog[open]").count()
            await page.locator("#prompt").fill(prompt)
            await expect(page.locator("#saveState")).to_have_text(
                "已保存", timeout=5000
            )
        await expect(page.locator(".film-frame")).to_have_count(5)
        for button, name, description in [
            ("createCharacterCard", "小林", "短发红衣"),
            ("createCharacterCard", "小陈", "黑发蓝衣"),
            ("createSceneCard", "咖啡馆", "暖光木桌"),
        ]:
            await page.locator(f"#{button}").click()
            await page.locator("#cardName").fill(name)
            await page.locator("#cardContent").fill(description)
            if name == "小林":
                await page.locator("#cardImage").set_input_files(
                    {
                        "name": "ref.svg",
                        "mimeType": "image/svg+xml",
                        "buffer": b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>',
                    }
                )
                await expect(page.locator("#cardImagePreview")).to_be_visible()
            await page.locator("#saveCard").click()
            await expect(page.locator("#cardDialog")).not_to_be_visible()
        assert project["character_cards"][0]["image_url"]
        await expect(page.locator("#libraryCards input")).to_have_count(3)
        await page.locator("#libraryNone").click()
        await expect(page.locator("#libraryCards input:checked")).to_have_count(0)
        await page.locator("#libraryAll").click()
        await expect(page.locator("#libraryCards input:checked")).to_have_count(3)
        await page.locator("#applyCards").click()
        await page.locator("#targetsAll").click()
        await expect(page.locator("#targetFrames input:checked")).to_have_count(5)
        await page.locator("#confirmApply").click()
        await expect(page.locator("#applyDialog")).not_to_be_visible()
        assert all(
            len(frame["character_card_ids"]) == 2 and len(frame["scene_card_ids"]) == 1
            for frame in frames
        )
        await expect(page.locator("#cardList input:checked")).to_have_count(3)
        await page.locator("#frameCardsNone").click()
        await expect(page.locator("#saveState")).to_have_text("已保存")
        assert frames[4]["character_card_ids"] == []
        assert len(frames[0]["character_card_ids"]) == 2
        await page.locator("#frameCardsAll").click()
        await expect(page.locator("#saveState")).to_have_text("已保存")
        await expect(page.locator("#cardList input:checked")).to_have_count(3)
        await page.locator('#cardList input[value="character-2"]').uncheck()
        await expect(page.locator("#saveState")).to_have_text("已保存")
        assert frames[4]["character_card_ids"] == ["character-1"]
        await page.locator("#libraryNone").click()
        await page.locator('#libraryCards input[value="scene-1"]').check()
        await page.locator("#applyCards").click()
        await page.locator("#targetsNone").click()
        await page.locator('#targetFrames input[value="1"]').check()
        await page.locator('#targetFrames input[value="3"]').check()
        await page.locator("#confirmApply").click()
        await expect(page.locator("#applyDialog")).not_to_be_visible()
        assert frames[0]["character_card_ids"] == frames[2]["character_card_ids"] == []
        assert len(frames[1]["character_card_ids"]) == 2
        await page.reload()
        await expect(page.locator("#cardList input:checked")).to_have_count(2)
        await expect(page.locator("#selectedPosition")).to_have_text("05 / 05")
        await expect(page.locator("#libraryCards input:checked")).to_have_count(1)
        await page.locator('.film-frame[data-id="5"] .frame-select').click()
        await expect(page.locator("#cardList input:checked")).to_have_count(2)
        await page.locator("#systemPrompt").fill(
            "电影质感、统一暖色调、保持角色服装一致"
        )
        await page.locator("#model").fill("my-custom-image-model")
        await page.locator("#sizePreset").select_option("1080x1920")
        await expect(page.locator("#size")).to_have_value("1080x1920")
        await expect(page.locator("#sizeInfo")).to_have_text("9:16 · 1080 × 1920")
        await page.locator("#size").fill("2304 × 1296")
        await expect(page.locator("#sizeInfo")).to_have_text("16:9 · 2304 × 1296")
        await expect(page.locator("#sizePreset")).to_have_value("")
        await expect(page.locator("#saveState")).to_have_text("已保存")
        await page.reload()
        await expect(page.locator("#connection")).to_have_text("已连接")
        await page.locator('.film-frame[data-id="5"] .frame-select').click()
        await expect(page.locator("#model")).to_have_value("my-custom-image-model")
        await expect(page.locator("#size")).to_have_value("2304x1296")
        await expect(page.locator("#systemPrompt")).to_have_value(
            "电影质感、统一暖色调、保持角色服装一致"
        )
        assert not await page.locator("#concurrency").count()
        await expect(page.locator("#batchGenerate")).to_be_disabled()
        await page.locator('.film-frame[data-id="5"] .frame-mark').click()
        await expect(page.locator("#selectedPosition")).to_have_text("05 / 05")
        await page.locator('.film-frame[data-id="5"] .frame-mark').click()
        for frame_id in ["1", "2", "3", "4"]:
            await page.locator(f'.film-frame[data-id="{frame_id}"] .frame-mark').click()
        await expect(page.locator(".frame-mark[aria-pressed='true']")).to_have_count(4)
        # Marking is saved and remains independent of which frame is enlarged.
        await page.wait_for_timeout(700)
        await page.reload()
        await expect(page.locator(".frame-mark[aria-pressed='true']")).to_have_count(4)
        await page.locator('.film-frame[data-id="1"] .frame-select').click()
        await page.locator("#batchGenerate").click()
        await expect(page.locator("#deleteFrame")).to_be_disabled()
        await expect(page.locator("#progress")).to_have_text(
            "3 / 5 已完成", timeout=10000
        )
        assert maximum == 4, maximum
        assert calls == 4
        assert frames[4]["image_url"] is None
        for frame_id in ["1", "2", "3", "4", "5"]:
            await page.locator(f'.film-frame[data-id="{frame_id}"] .frame-mark').click()
        await expect(page.locator(".frame-mark[aria-pressed='true']")).to_have_count(1)
        await page.locator("#batchGenerate").click()
        await expect(page.locator("#progress")).to_have_text(
            "4 / 5 已完成", timeout=10000
        )
        assert calls == 5
        assert frames[4]["system_prompt"] == "电影质感、统一暖色调、保持角色服装一致"
        assert frames[4]["model"] == "my-custom-image-model"
        assert frames[4]["size"] == "2304x1296"
        await page.locator('.film-frame[data-id="2"]').click()
        await expect(page.locator("#frameError")).to_have_text("测试上游失败，可重试")
        await page.locator("#prompt").fill("人物望向窗外，雨滴映着城市灯光")
        await page.locator("#generateFrame").click()
        await expect(page.locator("#progress")).to_have_text(
            "5 / 5 已完成", timeout=10000
        )
        assert frames[1]["prompt"] == "人物望向窗外，雨滴映着城市灯光"
        assert len(frames) == 5
        await page.locator('.film-frame[data-id="1"]').click()
        original = frames[0]["image_url"]
        await page.locator("#prompt").fill("广角远景，雨夜汽车驶过城市天际线")
        await page.locator("#generateFrame").click()
        await expect(page.locator("#previewStatus")).to_have_text(
            "已完成", timeout=10000
        )
        assert len(frames[0]["versions"]) == 2
        assert frames[0]["image_url"] != original
        assert frames[2]["prompt"] == "中景，人物推开咖啡馆的门"
        # Completed marked frames are updated even with the same prompt.
        untouched = [frames[index]["image_url"] for index in [1, 3, 4]]
        for frame_id in ["1", "3", "5"]:
            await page.locator(f'.film-frame[data-id="{frame_id}"] .frame-mark').click()
        await expect(page.locator(".frame-mark[aria-pressed='true']")).to_have_count(2)
        await page.locator("#batchGenerate").click()
        await expect(page.locator("#progress")).to_have_text(
            "5 / 5 已完成", timeout=10000
        )
        assert len(frames[0]["versions"]) == 3
        assert len(frames[2]["versions"]) == 2
        assert [frames[index]["image_url"] for index in [1, 3, 4]] == untouched
        assert calls == 9
        await page.locator('.film-frame[data-id="1"] .frame-select').click()
        await page.locator("#nextFrame").click()
        await expect(page.locator("#selectedPosition")).to_have_text("02 / 05")
        await page.reload()
        await expect(page.locator("#progress")).to_have_text("5 / 5 已完成")
        await page.locator('.film-frame[data-id="3"]').click()
        await expect(page.locator("#selectedPosition")).to_have_text("03 / 05")
        await page.wait_for_timeout(650)
        # Scrolling to a film center updates both the active frame and main preview.
        await page.evaluate("""() => {
            const viewport = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.getElementById('filmScroll');
            const frame = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.querySelector('.film-frame[data-id="4"]');
            const rect = frame.getBoundingClientRect(), outer = viewport.getBoundingClientRect();
            viewport.scrollLeft += rect.left + rect.width / 2 - outer.left - outer.width / 2;
        }""")
        await expect(page.locator("#selectedPosition")).to_have_text("04 / 05")
        await expect(
            page.locator(".film-frame.active .frame-picture img")
        ).to_have_attribute("src", frames[3]["image_url"])
        await page.wait_for_timeout(200)
        gallery_geometry = await page.evaluate("""() => {
            const viewport = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.getElementById('filmScroll').getBoundingClientRect();
            const current = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.querySelector('.film-frame.active');
            const center = current.getBoundingClientRect();
            const left = current.previousElementSibling.getBoundingClientRect();
            const right = current.nextElementSibling.getBoundingClientRect();
            return {
                centered: Math.abs(center.left + center.width / 2 - viewport.left - viewport.width / 2) < 3,
                fullyVisible: center.left > viewport.left && center.right < viewport.right,
                larger: center.width > left.width * 1.2 && center.width > right.width * 1.2,
                leftClipped: left.left < viewport.left && left.right > viewport.left,
                rightClipped: right.right > viewport.right && right.left < viewport.right,
                fullImage: getComputedStyle(current.querySelector('img')).objectFit === 'contain',
            };
        }""")
        assert all(gallery_geometry.values()), gallery_geometry
        await page.locator("#frameTitle").fill("04 · 一封未拆开的信")
        await expect(page.locator("#saveState")).to_have_text("已保存", timeout=5000)
        screenshot_dir = Path(args.screenshots)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(
            path=str(screenshot_dir / "storyboard-desktop.png"), full_page=True
        )
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(200)
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth"
        ), "Mobile page overflows horizontally"
        await page.locator("#nextFrame").click()
        await expect(page.locator("#selectedPosition")).to_have_text("05 / 05")
        await page.wait_for_timeout(700)
        await expect(page.locator(".film-frame.active")).to_have_attribute(
            "data-id", "5"
        )
        await expect(
            page.locator(".film-frame.active .frame-picture img")
        ).to_have_attribute("src", frames[4]["image_url"])
        geometry = await page.evaluate("""() => {
            const viewport = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.getElementById('filmScroll').getBoundingClientRect();
            const frame = document.querySelector('studio-workbench[data-workbench=storyboard]').shadowRoot.querySelector('.film-frame.active').getBoundingClientRect();
            return Math.abs(frame.left + frame.width / 2 - viewport.left - viewport.width / 2);
        }""")
        assert geometry < 15, f"Selected mobile film is not centered: {geometry}"
        await page.screenshot(
            path=str(screenshot_dir / "storyboard-mobile.png"), full_page=True
        )
        # Keyboard navigation still selects the matching editor and large image.
        await page.locator("#filmScroll").focus()
        await page.keyboard.press("ArrowLeft")
        await expect(page.locator("#selectedPosition")).to_have_text("04 / 05")
        await expect(page.locator("#frameTitle")).to_have_value("04 · 一封未拆开的信")
        await page.wait_for_timeout(700)
        # A vertical wheel gesture moves the unified film gallery horizontally.
        await page.locator("#filmScroll").hover()
        await page.mouse.wheel(0, -600)
        await expect(page.locator("#selectedPosition")).not_to_have_text("04 / 05")
        await expect(
            page.locator(".film-frame.active .frame-picture img")
        ).to_be_visible()
        # Canceling a deletion leaves the entire storyboard intact.
        await page.locator('.film-frame[data-id="3"] .frame-select').click()

        async def dismiss_delete(dialog):
            await dialog.dismiss()

        page.once("dialog", dismiss_delete)
        await page.locator("#deleteFrame").click()
        await expect(page.locator(".film-frame")).to_have_count(5)
        assert len(frames) == 5

        # Deleting the selected middle frame chooses its following neighbor.
        async def accept_delete(dialog):
            await dialog.accept()

        page.on("dialog", accept_delete)
        unaffected = {f["id"]: deepcopy(f) for f in frames if f["id"] != "3"}
        await page.locator("#prompt").fill("删除前尚未自动保存的修改")
        await page.locator("#deleteFrame").click()
        await expect(page.locator(".film-frame")).to_have_count(4)
        await expect(page.locator("#selectedPosition")).to_have_text("03 / 04")
        await expect(page.locator("#frameTitle")).to_have_value("04 · 一封未拆开的信")
        assert {f["id"]: f for f in frames} == unaffected
        await page.reload()
        await expect(page.locator(".film-frame")).to_have_count(4)
        # Film delete buttons can remove a different frame without changing focus.
        await page.locator('.film-frame[data-id="4"] .frame-select').click()
        await page.locator('.film-frame[data-id="5"] .frame-delete').click()
        await expect(page.locator(".film-frame")).to_have_count(3)
        await expect(page.locator("#frameTitle")).to_have_value("04 · 一封未拆开的信")
        while frames:
            remaining = len(frames) - 1
            await page.locator("#deleteFrame").click()
            await expect(page.locator(".film-frame")).to_have_count(remaining)
        await expect(page.locator("#deleteFrame")).to_be_disabled()
        await expect(page.locator("#prompt")).to_be_disabled()
        await expect(page.locator("#previewEmpty")).to_be_visible()
        await page.reload()
        await expect(page.locator("#connection")).to_have_text("已连接")
        await expect(page.locator(".film-frame")).to_have_count(0)
        await page.locator("#filmCreate").click()
        await expect(page.locator(".film-frame")).to_have_count(1)
        await expect(page.locator("#prompt")).to_have_value("")
        assert not errors, errors
        assert not errors, errors
        await page.goto(args.url + "/single")
        await expect(page.locator("#size")).to_have_value("2048x1152")
        await page.locator("#model").fill("another-custom-model")
        await page.locator("#sizePreset").select_option("1920x1080")
        await expect(page.locator("#sizeInfo")).to_have_text("16:9 · 1920 × 1080")
        await page.locator("#size").fill("2560x1600")
        await expect(page.locator("#sizeInfo")).to_have_text("16:10 · 2560 × 1600")
        await page.locator("#systemPrompt").fill("统一电影画风")
        # A fresh installation creates one default project and one frame.
        frames.clear()
        project.pop("frames_initialized", None)
        project.pop("workspace", None)
        project_available = False
        await page.goto(args.url + "/storyboard")
        await expect(page.locator("#connection")).to_have_text("已连接")
        await expect(page.locator(".film-frame")).to_have_count(1)
        await expect(page.locator("#prompt")).to_have_value("")
        assert project["name"] == "分镜项目"
        default_id = frames[0]["id"]
        await page.reload()
        await expect(page.locator(".film-frame")).to_have_count(1)
        assert frames[0]["id"] == default_id
        # Even an incomplete size and edits made while saving fails survive reload.
        fail_frame_saves = True
        await page.locator("#prompt").fill("断网时写下的画面，刷新后仍在")
        await page.locator("#systemPrompt").fill("断网时的系统提示词")
        await page.locator("#size").fill("1920x")
        await page.wait_for_timeout(600)
        assert frames[0]["prompt"] == ""
        await page.reload()
        await expect(page.locator("#prompt")).to_have_value(
            "断网时写下的画面，刷新后仍在"
        )
        await expect(page.locator("#systemPrompt")).to_have_value("断网时的系统提示词")
        await expect(page.locator("#size")).to_have_value("1920x")
        fail_frame_saves = False
        await page.evaluate("window.dispatchEvent(new Event('online'))")
        await expect(page.locator("#saveState")).to_have_text("已保存")
        assert frames[0]["size"] == "1920x"
        assert frames[0]["prompt"] == "断网时写下的画面，刷新后仍在"
        # Card form text is recoverable before the card has been submitted.
        await page.locator("#createSceneCard").click()
        await page.locator("#cardName").fill("未提交的场景")
        await page.locator("#cardContent").fill("草稿里的光线和天气")
        await page.reload()
        await page.locator("#createSceneCard").click()
        await expect(page.locator("#cardName")).to_have_value("未提交的场景")
        await expect(page.locator("#cardContent")).to_have_value("草稿里的光线和天气")
        await page.locator('[data-close="cardDialog"]').click()
        # The single-image page stores unsubmitted text in PostgreSQL too.
        await page.goto(args.url + "/single")
        await page.locator("#prompt").fill("单图页面未生成的提示词")
        await page.locator("#systemPrompt").fill("单图页面系统提示词")
        await page.wait_for_timeout(600)
        assert (
            project["workspace"]["single_image_draft"]["prompt"]
            == "单图页面未生成的提示词"
        )
        await page.reload()
        await expect(page.locator("#prompt")).to_have_value("单图页面未生成的提示词")
        await expect(page.locator("#systemPrompt")).to_have_value("单图页面系统提示词")
        # Switching editors keeps an in-flight generation attached to its frame.
        generation_gate = asyncio.Event()
        board = page.locator('studio-workbench[data-workbench="storyboard"]')
        single = page.locator('studio-workbench[data-workbench="single"]')
        await page.locator('.studio-links a[href="/storyboard"]').click()
        await expect(board).to_be_visible()
        await board.locator("#size").fill("1920x1080")
        await board.locator("#prompt").fill("切换工作台期间继续生成的画面")
        previous_calls = calls
        await board.locator("#generateFrame").click()
        await expect(board.locator("#generateFrame")).to_have_text("生成中…")
        await page.locator('.studio-links a[href="/single"]').click()
        await expect(single).to_be_visible()
        await expect(single.locator("#prompt")).to_have_value("单图页面未生成的提示词")
        generation_gate.set()
        await expect(board.locator("#previewStatus")).to_have_text("已完成")
        await page.locator('.studio-links a[href="/storyboard"]').click()
        await expect(board).to_be_visible()
        await expect(board.locator("#previewStatus")).to_have_text("已完成")
        await expect(board.locator("#prompt")).to_have_value(
            "切换工作台期间继续生成的画面"
        )
        assert calls == previous_calls + 1
        assert not errors, errors
        await browser.close()
    print(
        f"UI smoke passed: max concurrency={maximum}, generations={calls}, desktop/mobile screenshots={args.screenshots}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--screenshots", default="/tmp/workflow-storyboard-screenshots")
    asyncio.run(check(parser.parse_args()))
