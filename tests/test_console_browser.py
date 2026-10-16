"""Optional real-browser tests on an isolated PostgreSQL schema and local server."""

import asyncio
from pathlib import Path
import socket
from threading import Thread
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import uvicorn

from web.app import create_app
from web.routers import frames, images


@pytest.fixture
def browser_server(environment, monkeypatch):
    pytest.importorskip("playwright.async_api")
    if not Path("/usr/bin/google-chrome").exists():
        pytest.skip("Chrome is required for optional browser tests")

    def no_generation(**kwargs):
        raise AssertionError("Browser tests may never call the paid image API")

    for module in (frames, images):
        monkeypatch.setattr(module, "relay_generate_raw", no_generation)
        monkeypatch.setattr(module, "relay_edit_raw", no_generation)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("Browser test server failed to start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()


def test_named_api_profiles_settings_and_workbench_selection(browser_server):
    from playwright.async_api import async_playwright, expect

    async def check():
        errors = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000}
            )
            registered = await context.request.post(
                browser_server + "/photodream/api/auth/register",
                data={
                    "username": "profile_browser",
                    "password": "browser-password-123",
                },
            )
            assert registered.status == 201
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(browser_server + "/photodream/#account")
            settings = page.locator("#apiProfilesSection")
            await expect(settings.locator("#apiProfileForm")).to_be_hidden()
            for name, url, key in [
                ("主力接口", "https://primary.example/v1", "browser-primary-secret"),
                ("备用接口", "https://backup.example/v1", "browser-backup-secret"),
            ]:
                await settings.locator("#newApiProfile").click()
                await settings.locator('[name="name"]').fill(name)
                await settings.locator('[name="base_url"]').fill(url)
                await settings.locator('[name="api_key"]').fill(key)
                await settings.locator('[type="submit"]').click()
                await expect(settings.locator("#apiProfileForm")).to_be_hidden()
                await expect(settings.locator("#apiProfileList")).to_contain_text(name)
            data = await (
                await context.request.get(
                    browser_server + "/photodream/api/settings/api-profiles"
                )
            ).json()
            first = next(p for p in data["profiles"] if p["name"] == "主力接口")
            second = next(p for p in data["profiles"] if p["name"] == "备用接口")
            await settings.locator(
                f'[data-profile-action="edit"][data-profile-id="{first["id"]}"]'
            ).click()
            await expect(settings.locator('[name="api_key"]')).to_have_value("")
            await settings.locator('[name="name"]').fill("主力接口 · 已改名")
            await settings.locator('[type="submit"]').click()
            await expect(settings.locator("#apiProfileList")).to_contain_text(
                "主力接口 · 已改名"
            )
            await settings.locator(
                f'[data-profile-action="select"][data-profile-id="{second["id"]}"]'
            ).click()
            await expect(settings.locator("#apiProfileList .tag")).to_have_text(
                "当前使用"
            )
            await page.locator('.studio-links a[href="/photodream/single"]').click()
            single = page.locator('studio-workbench[data-workbench="single"]')
            await expect(single.locator("#apiProfileSelect")).to_have_value(
                second["id"]
            )
            await expect(single.locator("#apiKey, #baseUrl")).to_have_count(0)
            await single.locator("#apiProfileSelect").select_option(first["id"])
            await expect(single.locator("#apiProfileInfo")).to_have_text(
                first["base_url"]
            )
            await page.locator('.studio-links a[href="/photodream/storyboard"]').click()
            storyboard = page.locator('studio-workbench[data-workbench="storyboard"]')
            await expect(storyboard.locator("#apiProfileSelect")).to_have_value(
                first["id"]
            )
            await expect(storyboard.locator("#apiKey, #baseUrl")).to_have_count(0)
            await page.reload()
            await expect(storyboard.locator("#apiProfileSelect")).to_have_value(
                first["id"]
            )
            await page.evaluate("window.settingsNavigationMarker = true")
            await storyboard.locator("#manageApiProfiles").click()
            await expect(settings).to_be_visible()
            assert await page.evaluate("window.settingsNavigationMarker === true")
            page.once("dialog", lambda dialog: dialog.accept())
            await settings.locator(
                f'[data-profile-action="delete"][data-profile-id="{first["id"]}"]'
            ).click()
            await expect(settings.locator("#apiProfileList")).not_to_contain_text(
                "主力接口"
            )
            await page.locator('.studio-links a[href="/photodream/storyboard"]').click()
            await expect(storyboard.locator("#apiProfileSelect")).to_have_value("")
            await storyboard.locator("#manageApiProfiles").click()
            await page.set_viewport_size({"width": 390, "height": 844})
            await settings.locator("#newApiProfile").click()
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "settings mobile overflow"
            stored = await page.evaluate("JSON.stringify(localStorage)")
            assert (
                "browser-primary-secret" not in stored
                and "browser-backup-secret" not in stored
            )
            assert not errors, errors
            await browser.close()

    asyncio.run(check())


def test_console_registration_projects_teams_resources_and_workbenches(browser_server):
    from playwright.async_api import async_playwright, expect

    async def check():
        errors = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            alice = await browser.new_context(viewport={"width": 1440, "height": 1000})
            bob = await browser.new_context(viewport={"width": 1440, "height": 1000})
            page = await alice.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(browser_server + "/photodream/")
            await expect(page).to_have_url(
                browser_server + "/photodream/login?next=%2Fphotodream%2F"
            )
            await expect(page.locator("#displayLabel")).to_be_hidden()
            await page.locator("#registerTab").click()
            await page.locator('[name="username"]').fill("alice")
            await page.locator('[name="password"]').fill("browser-password-123")
            await page.locator('[name="display_name"]').fill("导演 Alice")
            await page.locator("#authSubmit").click()
            await expect(page.locator(".page-head h1")).to_have_text("你好，导演 Alice")
            await expect(page.locator("#adminMenu")).to_be_hidden()
            await expect(page.locator(".studio-links a")).to_have_count(6)
            await page.locator('[data-action="new-project"]').click()
            await page.locator('#dialogForm [name="name"]').fill("雨夜来信")
            await page.locator('#dialogForm [name="synopsis"]').fill(
                "一座城市，一封来信。"
            )
            await page.locator("#dialogSubmit").click()
            await expect(page.locator(".project-card h2")).to_have_text("雨夜来信")
            await page.locator('.studio-links a[href="/photodream/#projects"]').click()
            await page.locator('[data-action="edit"]').click()
            await page.locator('#dialogForm [name="name"]').fill("雨夜来信 · 第一季")
            await page.locator("#dialogSubmit").click()
            await expect(page.locator(".project-card h2")).to_have_text(
                "雨夜来信 · 第一季"
            )
            await page.locator("#projectSearch").fill("不存在")
            await expect(page.locator("#projectGrid .project-card")).to_have_count(0)
            await page.locator("#projectSearch").fill("")
            await page.locator(
                '.project-card a[href^="/photodream/storyboard"]'
            ).click()
            await expect(page.locator("#connection")).to_have_text("已连接")
            await expect(page.locator(".film-frame")).to_have_count(1)
            await expect(page.locator(".studio-nav")).to_have_count(1)
            await page.locator("#prompt").fill("雨夜街道，远景")
            await expect(page.locator("#saveState")).to_contain_text("已保存")
            await page.locator("#createCharacterCard").click()
            await page.locator("#cardName").fill("林先生")
            await page.locator("#cardContent").fill("成年男士，灰色风衣")
            await page.locator("#saveCard").click()
            await expect(page.locator("#libraryCards")).to_contain_text("林先生")
            await page.locator('.studio-links a[href="/photodream/#teams"]').click()
            await page.locator('[data-action="new-team"]').click()
            await page.locator('#dialogForm [name="name"]').fill("来信创作组")
            await page.locator("#dialogSubmit").click()
            await expect(page.locator(".team-list")).to_contain_text("来信创作组")
            await page.locator('[data-action="invite"]').click()
            invite = await page.locator(".invite-code").inner_text()
            member = await bob.new_page()
            member.on("pageerror", lambda error: errors.append(str(error)))
            await member.goto(browser_server + "/photodream/login")
            await member.locator("#registerTab").click()
            await member.locator('[name="username"]').fill("bob")
            await member.locator('[name="password"]').fill("browser-password-123")
            await member.locator("#authSubmit").click()
            await expect(member.locator(".page-head h1")).to_have_text("你好，bob")
            await expect(member.locator(".project-card")).to_have_count(0)
            await member.locator('.studio-links a[href="/photodream/#teams"]').click()
            await member.locator('[data-action="join-team"]').click()
            await member.locator('#dialogForm [name="token"]').fill(invite)
            await member.locator("#dialogSubmit").click()
            await expect(member.locator("#memberTable tbody tr")).to_have_count(2)
            await expect(member.locator('[data-action="invite"]')).to_have_count(0)
            await page.locator('.studio-links a[href="/photodream/#projects"]').click()
            await page.locator('[data-action="share"]').click()
            await page.locator('#dialogForm [name="team_id"]').select_option(
                label="来信创作组"
            )
            await page.locator("#dialogSubmit").click()
            await expect(page.locator(".project-card .tag")).to_have_text("来信创作组")
            await member.reload()
            await expect(member.locator(".project-card h2")).to_have_text(
                "雨夜来信 · 第一季"
            )
            await member.locator(
                '.studio-links a[href="/photodream/#resources"]'
            ).click()
            await expect(member.locator(".resource-card h3")).to_have_text("林先生")
            await member.locator(
                '.studio-links a[href="/photodream/#projects"]'
            ).click()
            await member.locator('[data-action="new-project"]').click()
            await member.locator('#dialogForm [name="name"]').fill("个人试验")
            await member.locator("#dialogSubmit").click()
            await expect(member.locator(".project-card")).to_have_count(2)
            await member.locator(
                '.studio-links a[href="/photodream/#resources"]'
            ).click()
            await member.locator("#resourceGrid summary").click()
            await member.locator('[data-action="import"]').click()
            await member.locator("#dialogSubmit").click()
            await expect(member.locator(".resource-card")).to_have_count(2)
            await page.reload()
            await expect(page.locator(".project-card")).to_have_count(1)
            await page.locator('.project-card a[href^="/photodream/single"]').click()
            await expect(page.locator("#projectSelect option:checked")).to_have_text(
                "雨夜来信 · 第一季"
            )
            await expect(page.locator("#characterCardList")).to_contain_text("林先生")
            await page.locator('.studio-links a[href="/photodream/#overview"]').click()
            await expect(page.locator(".stats")).to_be_visible()
            screenshots = Path("/tmp/workflow-console-screenshots")
            screenshots.mkdir(exist_ok=True)
            await page.screenshot(
                path=str(screenshots / "overview-desktop.png"), full_page=True
            )
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "mobile overflow"
            await page.screenshot(
                path=str(screenshots / "overview-mobile.png"), full_page=True
            )
            await page.locator('.studio-account a[href="/photodream/#account"]').click()
            await page.locator('#profileForm [name="display_name"]').fill("Alice 导演")
            await page.locator("#profileForm button").click()
            await expect(page.locator(".studio-account a")).to_have_text("Alice 导演")
            assert not errors, errors
            await browser.close()

    asyncio.run(check())


def test_generation_journal_downloads_and_resource_deletion(
    browser_server, monkeypatch
):
    from playwright.async_api import async_playwright, expect
    from test_images import png_bytes
    from web.services import images as image_files

    generated = []

    def relay(**kwargs):
        generated.append(kwargs)
        path = image_files.OUTPUT_DIR / f"browser_archive_{len(generated)}.png"
        path.write_bytes(png_bytes())
        return {"image_url": f"/outputs/{path.name}"}

    for module in (frames, images):
        monkeypatch.setattr(module, "relay_generate_raw", relay)
        monkeypatch.setattr(module, "relay_edit_raw", relay)

    async def check():
        errors = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1100}, accept_downloads=True
            )
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            assert (
                await page.request.post(
                    browser_server + "/photodream/api/auth/register",
                    data={
                        "username": "journal_director",
                        "password": "browser-password-123",
                    },
                )
            ).status == 201
            project = await (
                await page.request.post(
                    browser_server + "/photodream/api/projects",
                    form={"name": "日志电影"},
                )
            ).json()
            root = browser_server + f"/photodream/api/projects/{project['id']}"
            for kind in ("character", "scene"):
                assert (
                    await page.request.post(
                        root + "/cards",
                        multipart={
                            "card_type": kind,
                            "name": kind,
                            "content": "最初设定",
                            "image": {
                                "name": kind + ".png",
                                "mimeType": "image/png",
                                "buffer": png_bytes(),
                            },
                        },
                    )
                ).status == 200
            await page.goto(
                browser_server + f"/photodream/storyboard?project={project['id']}"
            )
            await expect(page.locator("#connection")).to_have_text("已连接")
            await page.locator("#systemPrompt").fill("电影感")
            await page.locator("#prompt").fill("第一版海边")
            await page.locator("#frameCardsAll").click()
            await page.locator("#generateFrame").click()
            await expect(page.locator("#previewStatus")).to_have_text("已完成")
            async with page.expect_download() as pending:
                await page.locator("#download").click()
            download = await pending.value
            assert download.suggested_filename == "browser_archive_1.png"
            assert Path(await download.path()).read_bytes() == png_bytes()
            await page.locator("#prompt").fill("第二版城市")
            await page.locator("#generateFrame").click()
            await expect(page.locator("#previewStatus")).to_have_text("已完成")
            await expect(page.locator("#versionList img")).to_have_count(2)
            await page.locator(
                '#libraryCards [data-card-action="image"][data-card-kind="scene"]'
            ).click()
            await expect(
                page.locator(
                    '#libraryCards [data-card-action="image"][data-card-kind="scene"]'
                )
            ).to_have_count(0)
            await page.locator(
                '#libraryCards [data-card-action="delete"][data-card-kind="character"]'
            ).click()
            await expect(
                page.locator('#cardList input[data-type="character"]')
            ).to_have_count(0)
            await page.locator("#removeFrameImage").click()
            await expect(page.locator("#download")).to_be_hidden()
            await page.locator("#generationHistory").click()
            await expect(page.locator(".page-head h1")).to_have_text("生成日志")
            await expect(page.locator(".generation-card")).to_have_count(2)
            await page.locator("[data-generation]").last.click()
            await expect(page.locator("#journalDetails")).to_be_visible()
            await expect(
                page.locator("#journalDetailBody textarea").nth(1)
            ).to_have_value("第一版海边")
            await expect(
                page.locator("#journalDetailBody textarea").nth(2)
            ).to_have_value(generated[0]["prompt"])
            await expect(
                page.locator("#journalDetailBody textarea").nth(3)
            ).to_contain_text("最初设定")
            async with page.expect_download() as pending:
                await page.locator("#journalDetailBody a[download]").click()
            assert (await pending.value).suggested_filename == "browser_archive_1.png"
            await page.locator("#journalClose").click()
            await page.locator('.console-side a[href="/photodream/#resources"]').click()
            await expect(page.locator(".resource-card")).to_have_count(1)
            await page.locator("#resourceGrid summary").click()
            await page.locator('.resource-card [data-card-action="delete"]').click()
            await expect(page.locator(".resource-card")).to_have_count(0)
            await page.locator('.studio-links a[href="/photodream/single"]').click()
            await expect(page.locator("#sceneCardList .card-chip")).to_have_count(0)
            await page.locator(
                'studio-workbench[data-workbench="single"] #prompt'
            ).fill("单图测试")
            await page.locator("#submitBtn").click()
            await expect(page.locator("#resultDownload")).to_be_visible()
            async with page.expect_download() as pending:
                await page.locator("#resultDownload").click()
            assert (await pending.value).suggested_filename == "browser_archive_3.png"
            await page.locator('.history-action[data-action="remove"]').click()
            await expect(page.locator(".history-item")).to_have_count(0)
            await page.locator(
                '.console-side a[href="/photodream/#generations"]'
            ).click()
            await expect(page.locator(".generation-card")).to_have_count(3)
            assert not errors, errors
            await browser.close()

    asyncio.run(check())


def test_project_groups_lazy_journal_images_and_physical_deletion(
    browser_server, monkeypatch
):
    from playwright.async_api import async_playwright, expect
    from test_images import png_bytes
    from web.services import images as image_files

    generated = []

    def relay(**kwargs):
        generated.append(kwargs)
        path = image_files.OUTPUT_DIR / f"purge_browser_{len(generated)}.png"
        path.write_bytes(png_bytes())
        return {"image_url": f"/outputs/{path.name}"}

    monkeypatch.setattr(images, "relay_generate_raw", relay)

    async def check():
        errors, asset_requests = [], []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            page = await browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.on(
                "request",
                lambda request: asset_requests.append(request.url)
                if "/photodream/outputs/" in request.url
                or "/photodream/uploads/" in request.url
                else None,
            )
            assert (
                await page.request.post(
                    browser_server + "/photodream/api/auth/register",
                    data={
                        "username": "asset_manager",
                        "password": "browser-password-123",
                    },
                )
            ).status == 201
            projects = []
            for name in ("电影甲", "电影乙"):
                project = await (
                    await page.request.post(
                        browser_server + "/photodream/api/projects", form={"name": name}
                    )
                ).json()
                projects.append(project)
                response = await page.request.post(
                    browser_server + f"/photodream/api/projects/{project['id']}/cards",
                    multipart={
                        "card_type": "scene",
                        "name": name + "场景",
                        "content": "日落",
                        "image": {
                            "name": "scene.png",
                            "mimeType": "image/png",
                            "buffer": png_bytes(),
                        },
                    },
                )
                assert response.status == 200
                assert (
                    await page.request.post(
                        browser_server + "/photodream/api/generate",
                        form={"project_id": project["id"], "prompt": name + "提示词"},
                    )
                ).status == 200
            await page.goto(browser_server + "/photodream/#generations")
            await expect(
                page.locator("#journalGrid .project-resource-group")
            ).to_have_count(2)
            await expect(page.locator("#journalGrid img")).to_have_count(0)
            assert not asset_requests
            await page.locator("#journalProject").select_option(projects[0]["id"])
            await expect(page.locator(".generation-card")).to_have_count(1)
            assert not asset_requests
            await page.locator("[data-generation]").click()
            await expect(
                page.locator("#journalDetailBody .journal-output img")
            ).to_be_visible()
            await expect(
                page.locator("#journalDetailBody textarea").nth(1)
            ).to_have_value("电影甲提示词")
            screenshots = Path("/tmp/photodream-project-resource-review")
            screenshots.mkdir(exist_ok=True)
            await page.screenshot(path=str(screenshots / "journal-detail-desktop.png"))
            output = "/outputs/purge_browser_1.png"
            await page.locator(f'[data-purge-image="{output}"]').click()
            await expect(page.locator(".journal-output")).to_contain_text(
                "图片已彻底删除"
            )
            assert not (image_files.OUTPUT_DIR / "purge_browser_1.png").exists()
            assert (
                await page.request.get(browser_server + "/photodream" + output)
            ).status == 404
            await expect(
                page.locator("#journalDetailBody .journal-output img")
            ).to_have_count(0)
            await expect(
                page.locator("#journalDetailBody textarea").nth(1)
            ).to_have_value("电影甲提示词")
            await page.locator("#journalClose").click()
            await expect(page.locator("#journalDetailBody img")).to_have_count(0)
            await page.locator('.console-side a[href="/photodream/#resources"]').click()
            await expect(
                page.locator("#resourceGrid .project-resource-group")
            ).to_have_count(2)
            await page.locator("#resourceProject").select_option(projects[0]["id"])
            await expect(page.locator(".resource-card")).to_have_count(1)
            purge_button = page.locator('[data-action="purge-resource"]')
            card_url = await purge_button.get_attribute("data-image-url")
            await purge_button.click()
            await expect(page.locator('[data-action="purge-resource"]')).to_have_count(
                0
            )
            await expect(page.locator(".resource-card")).to_contain_text("日落")
            assert not (image_files.UPLOAD_DIR / Path(card_url).name).exists()
            assert (
                await page.request.get(browser_server + "/photodream" + card_url)
            ).status == 404
            assert (
                await page.locator("#resourceProject").input_value()
                == projects[0]["id"]
            )
            for size in (
                {"width": 1440, "height": 1100},
                {"width": 390, "height": 844},
            ):
                await page.set_viewport_size(size)
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                await page.screenshot(
                    path=str(screenshots / f"resources-{size['width']}.png")
                )
            assert not errors, errors
            await browser.close()

    asyncio.run(check())


def test_existing_storyboard_regression(browser_server):
    import httpx
    from browser_storyboard import check

    response = httpx.post(
        browser_server + "/photodream/api/auth/register",
        json={"username": "smoke", "password": "browser-password-123"},
    )
    assert response.status_code == 201
    args = SimpleNamespace(
        url=browser_server,
        chrome="/usr/bin/google-chrome",
        screenshots="/tmp/workflow-storyboard-screenshots",
        session_cookie=response.cookies.get("workflow_session"),
    )
    asyncio.run(check(args))


def test_workbench_switch_preserves_shell_drafts_files_and_project(browser_server):
    import httpx
    from playwright.async_api import async_playwright, expect
    from test_images import png_bytes

    async def check():
        with httpx.Client(base_url=browser_server) as api:
            assert (
                api.post(
                    "/photodream/api/auth/register",
                    json={"username": "switcher", "password": "browser-password-123"},
                ).status_code
                == 201
            )
            first = api.post(
                "/photodream/api/projects", data={"name": "连续切换项目"}
            ).json()
            second = api.post(
                "/photodream/api/projects", data={"name": "另一个项目"}
            ).json()
            cookie = api.cookies.get("workflow_session")
        documents, requests, errors = [], [], []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1600, "height": 1000}
            )
            await context.add_cookies(
                [{"name": "workflow_session", "value": cookie, "url": browser_server}]
            )
            page = await context.new_page()
            page.on(
                "request",
                lambda r: (
                    documents.append(r.url) if r.resource_type == "document" else None,
                    requests.append(r.url),
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(
                f"{browser_server}/photodream/storyboard?project={first['id']}"
            )
            board = page.locator('studio-workbench[data-workbench="storyboard"]')
            single = page.locator('studio-workbench[data-workbench="single"]')
            await expect(board.locator("#connection")).to_have_text("已连接")
            await expect(
                page.locator('.console-side a[href="/photodream/storyboard"]')
            ).to_have_attribute("aria-current", "page")
            await page.evaluate(
                "window.shellMarker = 'preserved'; window.originalNav = document.querySelector('.studio-nav')"
            )
            await board.locator("#prompt").fill("分镜草稿：雨后的城市")
            await page.locator('.studio-links a[href="/photodream/single"]').click()
            await expect(single).to_be_visible()
            await expect(page.locator("#workbenchLoading")).to_be_hidden()
            await expect(single.locator("#projectSelect")).to_have_value(first["id"])
            await single.locator("#prompt").fill("单图草稿：晴天的城市")
            await single.locator("#referenceImages").set_input_files(
                {
                    "name": "reference.png",
                    "mimeType": "image/png",
                    "buffer": png_bytes(),
                }
            )
            for _ in range(3):
                await page.locator(
                    '.studio-links a[href="/photodream/storyboard"]'
                ).click()
                await expect(board).to_be_visible()
                await expect(board.locator("#prompt")).to_have_value(
                    "分镜草稿：雨后的城市"
                )
                await page.locator('.studio-links a[href="/photodream/single"]').click()
                await expect(single).to_be_visible()
                await expect(single.locator("#prompt")).to_have_value(
                    "单图草稿：晴天的城市"
                )
                assert (
                    await single.locator("#referenceImages").evaluate(
                        "input => input.files[0].name"
                    )
                    == "reference.png"
                )
            assert await page.evaluate(
                "window.shellMarker === 'preserved' && originalNav === document.querySelector('.studio-nav')"
            )
            assert len(documents) == 1, documents
            assert (
                len(
                    [url for url in requests if url.endswith("/photodream/api/auth/me")]
                )
                == 1
            )
            assert (
                len([url for url in requests if url.endswith("/photodream/api/config")])
                == 1
            )
            assert (
                len([url for url in requests if url.endswith("/frames/initialize")])
                == 1
            )
            assert (
                await page.evaluate("performance.getEntriesByType('navigation').length")
                == 1
            )
            await page.go_back()
            await expect(board).to_be_visible()
            await board.locator("#createCharacterCard").click()
            await expect(board.locator("#cardDialog")).to_be_visible()
            await board.locator("#cardName").fill("切换前未提交的角色")
            await page.go_forward()
            await expect(single).to_be_visible()
            await expect(board.locator("#cardDialog")).not_to_have_attribute("open", "")
            await page.locator('.studio-links a[href="/photodream/storyboard"]').click()
            await board.locator("#createCharacterCard").click()
            await expect(board.locator("#cardName")).to_have_value("切换前未提交的角色")
            await board.locator('[data-close="cardDialog"]').click()
            await page.locator('.studio-links a[href="/photodream/single"]').click()
            await expect(single).to_be_visible()
            await single.locator("#projectSelect").select_option(second["id"])
            await expect(single.locator("#prompt")).to_have_value("")
            assert (
                await single.locator("#referenceImages").evaluate(
                    "input => input.files.length"
                )
                == 0
            )
            await single.locator("#projectSelect").select_option(first["id"])
            await expect(single.locator("#prompt")).to_have_value(
                "单图草稿：晴天的城市"
            )
            await single.locator("#projectSelect").select_option(second["id"])
            await page.locator('.studio-links a[href="/photodream/storyboard"]').click()
            await expect(board.locator("#projectSelect")).to_have_value(second["id"])
            await expect(board.locator(".film-frame")).to_have_count(1)
            await page.evaluate(
                "Studio.navigate('/photodream/single'); Studio.navigate('/photodream/#projects'); Studio.navigate('/photodream/storyboard')"
            )
            await expect(board).to_be_visible()
            await expect(page.locator("#workbenchLoading")).to_be_hidden()
            await expect(page.locator(".studio-nav")).to_have_count(1)
            assert await page.evaluate("location.pathname === '/photodream/storyboard'")
            screenshots = Path("/tmp/workflow-console-screenshots")
            screenshots.mkdir(exist_ok=True)
            await page.screenshot(
                path=str(screenshots / "integrated-storyboard.png"), full_page=True
            )
            await page.locator('.studio-links a[href="/photodream/single"]').click()
            await expect(single).to_be_visible()
            await page.screenshot(
                path=str(screenshots / "integrated-single.png"), full_page=True
            )
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            assert await page.locator("#workspaceMain").evaluate(
                "main => main.scrollHeight > main.clientHeight && main.getBoundingClientRect().bottom <= innerHeight + 1"
            )
            await single.locator("#submitBtn").scroll_into_view_if_needed()
            await expect(single.locator("#submitBtn")).to_be_in_viewport()
            await expect(page.locator(".studio-nav")).to_be_in_viewport()
            await page.locator("#workspaceMain").evaluate("main => main.scrollTop = 0")
            await page.screenshot(
                path=str(screenshots / "integrated-single-mobile.png"), full_page=True
            )
            assert not errors, errors
            assert all(
                urlparse(url).path.startswith("/photodream/")
                for url in requests
                if urlparse(url).scheme in {"http", "https"}
            ), requests
            await browser.close()

    asyncio.run(check())
