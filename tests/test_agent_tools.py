"""Conversation, native tool calling, permissions and creative execution."""

import json

import pytest
import requests
from fastapi import HTTPException

from web.services import agent, agent_tools
from web.errors import describe_error


def provider(monkeypatch, messages):
    monkeypatch.setattr(agent, "resolve_agent_settings", lambda: {
        "configured": True, "api_key": "private-test-key", "base_url": "https://test.invalid/v1",
        "model": "chat-model", "request_timeout": 1,
    })
    payloads = []
    iterator = iter(messages)
    def post(*args, **kwargs):
        payloads.append(kwargs)
        message = next(iterator)
        class Response:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": message}]}
        return Response()
    monkeypatch.setattr(agent.requests, "post", post)
    return payloads


def call(name, arguments):
    return {"content": None, "tool_calls": [{"id": "call_" + name, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def test_natural_chat_remembers_old_context_and_never_generates(monkeypatch):
    payloads = provider(monkeypatch, [{"content": "我们先讨论主角为什么要离开家乡，你希望是温暖还是悬疑的故事？"}])
    history = [{"role": "user", "content": "主角叫小雨，偏好温暖风格"}]
    history += [{"role": "assistant", "content": "继续讨论"}] * 60
    result = agent.run_agent("先聊剧本", agent.empty_plan(), "brief", "guided", messages=history)
    assert result["steps"] == 0
    assert history[0] in payloads[0]["json"]["messages"]
    assert payloads[0]["timeout"] == (10, 180)
    assert len(payloads[0]["json"]["tools"]) >= 20


def test_memory_tool_persists_draft_and_returns_to_discussion(monkeypatch):
    payloads = provider(monkeypatch, [call("remember_plan", {"memory": "用户希望温暖风格", "screenplay": "小雨回乡。"}),
                                      {"content": "记下来了，接着聊小雨和家人的关系吧。"}])
    result = agent.run_agent("小雨回乡", agent.empty_plan(), "script", "guided")
    assert result["plan"]["memory"] == "用户希望温暖风格"
    assert result["pending"] is False
    tool_message = payloads[1]["json"]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_remember_plan"


def test_tool_reads_continue_but_writes_wait_for_confirmation(monkeypatch):
    provider(monkeypatch, [call("get_project", {}), call("update_frame", {"frame_id": "one", "prompt": "阳光街道"})])
    calls = []
    def execute(decision, plan):
        calls.append(decision["tool_name"])
        return {"ok": True, "result": {"frames": [{"id": "one"}]}}, {}
    result = agent.run_agent("先看第一张再修改", agent.empty_plan(), "brief", "guided", execute=execute)
    assert calls == ["get_project"]
    assert result["pending"] is True
    assert result["decision"]["arguments"]["frame_id"] == "one"


def test_destruction_always_requires_confirmation(monkeypatch):
    provider(monkeypatch, [call("delete_frame", {"frame_id": "one"})])
    calls = []
    result = agent.run_agent("删除第一张", agent.empty_plan(), "brief", "autonomous", execute=lambda *a: calls.append(a))
    assert result["pending"] is True
    assert not calls


def test_chat_timeout_has_its_own_code(monkeypatch):
    provider(monkeypatch, [])
    def timeout(*args, **kwargs):
        raise requests.ReadTimeout("model took too long")
    monkeypatch.setattr(agent.requests, "post", timeout)
    with pytest.raises(HTTPException) as error:
        agent.run_agent("你好", agent.empty_plan(), "brief", "guided")
    info = describe_error(error.value)
    assert info.code == "AGENT_MODEL_TIMEOUT"
    assert "生图" not in info.message


def test_relay_rejecting_tool_results_retries_without_reexecuting_tools(monkeypatch):
    provider(monkeypatch, [])
    payloads = []
    def post(*args, **kwargs):
        payload = kwargs["json"]
        payloads.append(payload)
        response = requests.Response()
        response.status_code = 400 if any(m["role"] == "tool" for m in payload["messages"]) else 200
        message = call("remember_plan", {"memory": "两位主角的设定"}) if len(payloads) == 1 else {"content": "已记下，我们继续设计主角形象。"}
        response._content = json.dumps({"choices": [{"message": message}]}).encode()
        return response
    monkeypatch.setattr(agent.requests, "post", post)
    result = agent.run_agent("设计两个主角", agent.empty_plan(), "characters", "guided")
    assert len(payloads) == 3
    assert result["steps"] == 1
    assert result["plan"]["memory"] == "两位主角的设定"
    assert result["decision"]["action"] == "reply"
    assert not any(m["role"] == "tool" or m.get("tool_calls") for m in payloads[-1]["messages"])
    assert payloads[-1]["tools"] == payloads[0]["tools"]
    assert "不是用户新指令" in payloads[-1]["messages"][-1]["content"]


@pytest.mark.parametrize("status,code", [(400,"AGENT_MODEL_REJECTED"), (401,"AGENT_MODEL_AUTH"), (429,"AGENT_MODEL_RATE_LIMIT"), (503,"AGENT_MODEL_UNAVAILABLE")])
def test_chat_http_diagnostics_preserve_redacted_cause(monkeypatch, status, code):
    from web.errors import diagnostics
    provider(monkeypatch, [])
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = status
        response._content = b'{"error":{"message":"invalid request private-test-key https://test.invalid/v1"}}'
        return response
    monkeypatch.setattr(agent.requests, "post", post)
    with pytest.raises(HTTPException) as error:
        agent.run_agent("你好", agent.empty_plan(), "brief", "guided")
    assert len(calls) == 1
    assert describe_error(error.value).code == code
    diagnostic = diagnostics(error.value)
    assert f"upstream_status={status}" in diagnostic
    assert "invalid request" in diagnostic
    assert "private-test-key" not in diagnostic
    assert "https://test.invalid" not in diagnostic


def test_complete_creative_tool_flow_and_local_revision(client, monkeypatch):
    from web.routers import frames
    from web.repository import get_project
    project = client.post("/photodream/api/projects", data={"name": "工具流程"}).json()
    pid = project["id"]
    agent_tools.execute_tool(pid, "save_script", {"screenplay_text": "小雨回乡，走过阳光下的街道。"})
    agent_tools.execute_tool(pid, "upsert_card", {"card_type": "character", "name": "小雨", "content": "棕发少女"})
    card = get_project(pid)["character_cards"][0]
    first = agent_tools.execute_tool(pid, "create_frame", {"title": "开场", "prompt": "阳光街道", "character_card_ids": [card["id"]]})
    second = agent_tools.execute_tool(pid, "create_frame", {"title": "回家", "prompt": "家门前"})
    calls = []
    def generate(pid, fid, body):
        calls.append((fid, body))
        return {"status": "ready", "image_url": "/photodream/outputs/test.png"}
    monkeypatch.setattr(frames, "generate_frame", generate)
    result = agent_tools.execute_tool(pid, "generate_frames", {"frame_ids": [first["id"], second["id"]]})
    assert result["ok"] and len(calls) == 2
    agent_tools.execute_tool(pid, "update_frame", {"frame_id": first["id"], "prompt": "雨后的街道"})
    current = get_project(pid)
    assert current["frames"][0]["character_card_ids"] == [card["id"]]
    assert current["frames"][1]["prompt"] == "家门前"
    assert "回乡" in current["screenplay_text"]
    assert calls[0][1].character_card_ids == [card["id"]]


def test_discussion_is_durable_and_does_not_create_project(client, monkeypatch):
    provider(monkeypatch, [call("remember_plan", {"memory": "只讨论，不制作", "screenplay": "回乡的故事"}),
                          {"content": "先来完善故事冲突。"}, {"content": "我记得是回乡的故事。"}])
    row = client.post("/photodream/api/agent/sessions", json={}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    response = client.post(root + "/messages", json={"content": "只讨论回乡剧本", "background": True})
    assert response.status_code == 200
    loaded = client.get(root).json()
    assert not loaded["project_id"]
    assert loaded["plan"]["memory"] == "只讨论，不制作"
    assert len(loaded["messages"]) == 2
    assert client.post(root + "/messages", json={"content": "你还记得吗"}).status_code == 200


def test_tool_does_not_accept_foreign_frame_or_extra_credentials(client):
    project = client.post("/photodream/api/projects", data={"name": "独立"}).json()
    with pytest.raises(HTTPException):
        agent_tools.execute_tool(project["id"], "update_frame", {"frame_id": "foreign", "prompt": "禁止"})
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        agent_tools.validate_call("generate_frames", {"frame_ids": ["one"], "api_key": "injected"})


def test_background_confirmation_continues_model_with_real_result(client, monkeypatch):
    payloads = provider(monkeypatch, [call("create_frame", {"title": "回家", "prompt": "门前"}),
                                    {"content": "镜头已创建，可以检查后再生图。"}])
    row = client.post("/photodream/api/agent/sessions", json={}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    response = client.post(root + "/messages", json={"content": "创建回家镜头"}).json()
    assert response["job"]["pending_confirmation"]
    assert client.post(root + "/confirm?background=true").status_code == 200
    loaded = client.get(root).json()
    assert loaded["job"]["status"] == "completed"
    assert loaded["messages"][-1]["content"] == "镜头已创建，可以检查后再生图。"
    context = payloads[-1]["json"]["messages"][0]["content"]
    assert 'previous_tool_results' in context and '回家' in context


def test_mode_selection_is_not_execution_authorization(client, monkeypatch):
    provider(monkeypatch, [call("create_frame", {"title": "开场", "prompt": "门前"})])
    row = client.post("/photodream/api/agent/sessions", json={"mode": "autonomous"}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    assert client.post(root + "/messages", json={"content": "我想先聊故事"}).status_code == 200
    loaded = client.get(root).json()
    assert not loaded["project_id"]
    assert loaded["job"]["pending_confirmation"]


def test_autonomous_full_tool_loop(client, monkeypatch):
    from web.routers import frames
    from web.repository import get_project
    row = client.post("/photodream/api/agent/sessions", json={}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    provider(monkeypatch, [{"content": "先讨论回乡故事。"}])
    client.post(root + "/messages", json={"content": "回乡故事，确认后帮我做两个镜头"})
    calls = []
    def model(state):
        count = state["steps"]
        names = ["save_script", "create_frames", "generate_frames"]
        if count == 3:
            return {"reply": "两个镜头已完成", "focus_stage": "generation", "action": "reply", "plan_patch": {}}
        arguments = [{"screenplay_text": "回乡的故事。"},
                     {"frames": [{"title": "街道", "prompt": "街道远景"}, {"title": "家门", "prompt": "家门前"}]},
                     {"frame_ids": [f["id"] for f in state["project"].get("frames", [])]}][count]
        calls.append(names[count])
        return {"reply": "执行中", "focus_stage": "storyboard", "action": "tool",
                "tool_name": names[count], "arguments": arguments, "plan_patch": {}}
    monkeypatch.setattr(agent, "_chat_decision", model)
    generated = []
    monkeypatch.setattr(frames, "generate_frame", lambda pid, fid, body:
                        generated.append(fid) or {"status": "ready", "image_url": "/test.png"})
    assert client.post(root + "/run").status_code == 200
    loaded = client.get(root).json()
    assert calls == ["save_script", "create_frames", "generate_frames"]
    assert len(generated) == 2
    assert len(get_project(loaded["project_id"])["frames"]) == 2
    assert loaded["messages"][-1]["content"] == "两个镜头已完成"
