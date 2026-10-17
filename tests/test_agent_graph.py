"""The agent chooses subjects and tools from model decisions, not a stage list."""

import pytest
from fastapi import HTTPException

from web.services import agent


def decision(stage="storyboard", action="reply", **kwargs):
    return {"reply": "收到", "focus_stage": stage, "action": action,
            "plan_patch": {}, "frame_ids": [], "requires_confirmation": False, **kwargs}


def test_model_can_jump_to_storyboard_without_fabricating_characters(monkeypatch):
    monkeypatch.setattr(agent, "_chat_decision", lambda state: decision(plan_patch={
        "shots": [{"title": "开场", "prompt": "雪地远景", "order": 1}],
    }))
    result = agent.run_agent("只做一个雪景镜头", agent.empty_plan(), "brief", "guided")
    assert result["stage"] == "storyboard"
    assert result["plan"]["characters"] == []
    assert len(result["plan"]["shots"]) == 1


def test_guided_model_action_pauses_before_tool_execution(monkeypatch):
    monkeypatch.setattr(agent, "_chat_decision", lambda state: decision(action="generate", frame_ids=["third"]))
    calls = []
    result = agent.run_agent("重做第三张", agent.empty_plan(), "generation", "guided", execute=lambda *args: calls.append(args))
    assert result["pending"] is True
    assert calls == []


def test_autonomous_tools_feed_results_back_into_model(monkeypatch):
    states = []
    def model(state):
        states.append(state)
        return decision(action="generate", frame_ids=["third"]) if not state["observations"] else decision()
    monkeypatch.setattr(agent, "_chat_decision", model)
    calls = []
    def execute(action, plan):
        calls.append(action["frame_ids"])
        return {"ok": True, "message": "第三张已生成"}, {"frames": [{"id": "third", "status": "completed"}]}
    result = agent.run_agent("只重做第三张", agent.empty_plan(), "generation", "autonomous", execute=execute)
    assert calls == [["third"]]
    assert len(states) == 2
    assert states[1]["observations"][0]["ok"]
    assert result["steps"] == 1


def test_tool_error_is_observed_by_model(monkeypatch):
    def model(state):
        if state["observations"]:
            assert state["observations"][0]["ok"] is False
            return decision(reply="镜头不存在，请重新选择")
        return decision(action="generate", frame_ids=["missing"])
    monkeypatch.setattr(agent, "_chat_decision", model)
    def execute(*args):
        raise HTTPException(404, "镜头不存在")
    result = agent.run_agent("重做", agent.empty_plan(), "brief", "autonomous", execute=execute)
    assert result["decision"]["reply"] == "镜头不存在，请重新选择"


def test_duplicate_generation_is_stopped(monkeypatch):
    monkeypatch.setattr(agent, "_chat_decision", lambda state: decision(action="generate", frame_ids=["one"]))
    calls = []
    def execute(*args):
        calls.append(True)
        return {"ok": True}, {}
    with pytest.raises(HTTPException, match="409"):
        agent.run_agent("生成", agent.empty_plan(), "brief", "autonomous", execute=execute)
    assert len(calls) == 1


def test_local_modification_preserves_other_shots():
    plan = agent.empty_plan()
    plan["shots"] = [{"id": "one", "title": "第一张", "prompt": "旧画面"},
                     {"id": "two", "title": "第二张", "prompt": "保留画面"}]
    result = agent.merge_plan(plan, {"shots": [{"id": "one", "title": "新标题", "prompt": "新画面"}]})
    assert len(result["shots"]) == 2
    assert result["shots"][1]["prompt"] == "保留画面"
    assert plan["shots"][0]["prompt"] == "旧画面"


def test_missing_configuration_does_not_invent_a_story(monkeypatch):
    monkeypatch.setattr(agent, "resolve_agent_settings", lambda: {"configured": False, "api_key": ""})
    with pytest.raises(HTTPException):
        agent.run_agent("你好", agent.empty_plan(), "brief", "guided")


def test_malformed_model_output_never_executes(monkeypatch):
    monkeypatch.setattr(agent, "resolve_agent_settings", lambda: {
        "configured": True, "api_key": "secret", "base_url": "https://test.invalid/v1", "model": "test", "request_timeout": 10,
    })
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": '{"action":"delete_all"}'}}]}
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: Response())
    calls = []
    result = agent.run_agent("你好", agent.empty_plan(), "brief", "autonomous", execute=lambda *a: calls.append(a))
    assert calls == []
    assert result["decision"]["action"] == "reply"
