"""Real-browser coverage for dialogs and administrator exception review."""

import asyncio

from sqlalchemy.orm import Session

from test_console_browser import browser_server as _browser_server
from web.errors import UpstreamError
from web.models import User
from web.routers import frames

browser_server = _browser_server


def test_login_error_dialog_preserves_inputs_and_allows_retry(browser_server):
    from playwright.async_api import async_playwright, expect

    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            context = await browser.new_context(viewport={"width": 390, "height": 844})
            user = {"username": "error_login", "password": "test-password-123"}
            assert (
                await context.request.post(
                    browser_server + "/photodream/api/auth/register", data=user
                )
            ).status == 201
            await context.clear_cookies()
            page = await context.new_page()
            await page.goto(browser_server + "/photodream/login")
            await page.locator('[name="username"]').fill(user["username"])
            await page.locator('[name="password"]').fill("wrong-password")
            await page.locator("#authSubmit").click()
            dialog = page.locator("#systemErrorDialog")
            await expect(dialog).to_be_visible()
            await expect(dialog.locator("#systemErrorList")).to_contain_text(
                "用户名或密码不正确"
            )
            await expect(page.locator("#authError")).to_be_empty()
            assert await dialog.locator("details").get_attribute("open") is None
            await dialog.locator("summary").click()
            await expect(dialog.locator("pre")).to_contain_text("诊断编号：")
            assert "wrong-password" not in await dialog.inner_text()
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            await dialog.locator("#dismissSystemError").click()
            await expect(page.locator('[name="username"]')).to_have_value(
                user["username"]
            )
            await expect(page.locator('[name="password"]')).to_have_value(
                "wrong-password"
            )
            # A new explicit retry must display the same category again.
            await page.locator("#authSubmit").click()
            await expect(dialog).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(dialog).not_to_be_visible()
            await page.locator('[name="password"]').fill(user["password"])
            await page.locator("#authSubmit").click()
            await expect(page).to_have_url(browser_server + "/photodream/")
            await expect(page.locator("#exceptionMenu")).to_be_hidden()
            await browser.close()

    asyncio.run(check())


def test_batch_errors_group_dialog_and_admin_review(
    browser_server, environment, monkeypatch
):
    from playwright.async_api import async_playwright, expect

    def fail(**kwargs):
        raise UpstreamError(
            401,
            "<html>private-browser-key https://relay.example/auth "
            + "upstream-details " * 500
            + "</html>",
        )

    monkeypatch.setattr(frames, "relay_generate_raw", fail)

    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000}
            )
            base = browser_server + "/photodream/api"
            registered = await context.request.post(
                base + "/auth/register",
                data={"username": "error_admin", "password": "test-password-123"},
            )
            actor = await registered.json()
            with Session(environment[1]) as session, session.begin():
                session.get(User, actor["id"]).is_admin = True
            profile = await context.request.post(
                base + "/settings/api-profiles",
                data={
                    "name": "test",
                    "base_url": "https://relay.example/v1",
                    "api_key": "private-browser-key",
                },
            )
            assert profile.status == 201
            project = await (
                await context.request.post(
                    base + "/projects", multipart={"name": "批量异常"}
                )
            ).json()
            frame_base = base + f"/projects/{project['id']}/frames"
            first = (
                await (await context.request.post(frame_base + "/initialize")).json()
            )[0]
            second = await (await context.request.post(frame_base, data={})).json()
            for index, frame in enumerate([first, second]):
                response = await context.request.post(
                    frame_base + "/" + frame["id"],
                    data={"prompt": f"测试画面 {index}", "marked": True},
                )
                assert response.status == 200
            page = await context.new_page()
            await page.add_init_script(
                "window.unexpectedErrors = []; window.addEventListener('error', e => { if(e.error) window.unexpectedErrors.push(e.message); });"
            )
            await page.goto(
                browser_server + f"/photodream/storyboard?project={project['id']}"
            )
            storyboard = page.locator('studio-workbench[data-workbench="storyboard"]')
            await expect(storyboard.locator("#batchGenerate")).to_be_enabled()
            await storyboard.locator("#batchGenerate").click()
            dialog = page.locator("#systemErrorDialog")
            await expect(dialog).to_be_visible()
            await expect(dialog.locator("#systemErrorList li")).to_have_count(1)
            await expect(dialog.locator("#systemErrorList")).to_contain_text(
                "2 次操作遇到同类问题"
            )
            await expect(dialog.locator("#systemErrorList")).to_contain_text(
                "生图接口认证失败"
            )
            await expect(storyboard.locator("#batchGenerate")).to_be_enabled()
            await dialog.locator("summary").click()
            diagnostic = await dialog.locator("pre").inner_text()
            assert diagnostic.count("诊断编号：") == 2
            for value in (
                "<html>",
                "upstream-details",
                "private-browser-key",
                "relay.example",
            ):
                assert value not in await dialog.inner_text()
            await dialog.locator("#dismissSystemError").click()
            await expect(storyboard.locator("#prompt")).to_have_value("测试画面 0")
            # Errors raised over an existing editor modal must leave it intact.
            await storyboard.locator("#createCharacterCard").click()
            await storyboard.locator("#cardName").fill("未保存的角色")
            await storyboard.locator("#cardContent").fill("未保存的设定")
            await page.route(
                "**/api/projects/*/cards",
                lambda route: route.fulfill(
                    status=502,
                    content_type="text/html",
                    body="<html>" + "long proxy failure" * 500 + "</html>",
                ),
            )
            await storyboard.locator("#saveCard").click()
            await expect(dialog).to_be_visible()
            await expect(dialog.locator("#systemErrorList")).to_contain_text(
                "服务返回异常"
            )
            await page.keyboard.press("Escape")
            await expect(storyboard.locator("#cardDialog")).to_be_visible()
            await expect(storyboard.locator("#cardName")).to_have_value("未保存的角色")
            await expect(storyboard.locator("#saveCard")).to_be_enabled()
            await page.keyboard.press("Escape")
            await page.locator("#exceptionMenu").click()
            await page.locator("#exceptionCode").fill("UPSTREAM_AUTH")
            await page.locator("#exceptionSearch").click()
            await expect(page.locator("#exceptionTable tbody tr")).to_have_count(2)
            await page.locator("[data-exception-detail]").first.click()
            details = page.locator("#exceptionDetails")
            await expect(details).to_be_visible()
            await expect(details.locator("pre")).to_contain_text("upstream_status=401")
            assert "private-browser-key" not in await details.inner_text()
            await page.locator("#exceptionClose").click()
            await page.locator("[data-exception-resolve]").first.click()
            await page.locator("#exceptionState").select_option("true")
            await expect(page.locator("#exceptionTable tbody tr")).to_have_count(1)
            await expect(page.locator("#exceptionTable")).to_contain_text("已处理")
            await page.reload()
            await expect(page.locator("#exceptionTable")).to_contain_text(
                "UPSTREAM_AUTH"
            )
            assert not await page.evaluate("window.unexpectedErrors"), (
                await page.evaluate("window.unexpectedErrors")
            )
            await browser.close()

    asyncio.run(check())
