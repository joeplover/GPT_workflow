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
