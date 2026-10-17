def test_agent_conversation_and_stage_materialization(client, monkeypatch):
    from web.services import agent
    monkeypatch.setattr(agent, "_chat_decision", lambda state: {
        "reply": "先检查这一镜头", "focus_stage": "storyboard", "action": "save",
        "plan_patch": {"shots": [{"title": "雨夜", "prompt": "雨夜街头", "order": 1}]},
    })
    created = client.post(
        "/photodream/api/agent/sessions", json={"mode": "guided"}
    )
    assert created.status_code == 201
    session = created.json()

    response = client.post(
        f"/photodream/api/agent/sessions/{session['id']}/messages",
        json={"content": "我想做一个雨夜里寻找旧相机的电影感短片"},
    )
    assert response.status_code == 200
    session = response.json()
    assert len(session["messages"]) == 2
    assert session["plan"]["characters"] == []
    assert session["stage"] == "storyboard"

    confirmed = client.post(
        f"/photodream/api/agent/sessions/{session['id']}/confirm"
    )
    assert confirmed.status_code == 200
    session = confirmed.json()
    assert session["project_id"]
    assert session["stage"] == "storyboard"

    assert client.post(f"/photodream/api/agent/sessions/{session['id']}/confirm").status_code == 409
    project = client.get(f"/photodream/api/projects/{session['project_id']}").json()
    assert project["screenplay_text"] == ""
    assert project["character_cards"] == []
    assert project["scene_cards"] == []
    assert len(project["frames"]) == 1
    assert all(frame["marked"] for frame in project["frames"])


def test_agent_sessions_are_project_scoped(client):
    first = client.post(
        "/photodream/api/agent/sessions", json={"project_id": "missing"}
    )
    assert first.status_code == 404


def test_confirm_generates_only_selected_frame_and_session_can_continue(client, monkeypatch):
    from web.services import agent
    from web.routers import frames
    from web.models import AgentSession
    from web import database
    from sqlalchemy.orm import Session

    current = {"reply": "准备两个镜头", "focus_stage": "storyboard", "action": "save",
               "plan_patch": {"shots": [{"title": "一", "prompt": "街道"}, {"title": "二", "prompt": "山峰"}]}}
    monkeypatch.setattr(agent, "_chat_decision", lambda state: current)
    row = client.post("/photodream/api/agent/sessions", json={}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    assert client.post(root + "/messages", json={"content": "两个镜头"}).status_code == 200
    confirmed = client.post(root + "/confirm").json()
    project = client.get(f"/photodream/api/projects/{confirmed['project_id']}").json()
    selected = project["frames"][1]["id"]
    current = {"reply": "仅重做第二张", "focus_stage": "generation", "action": "generate", "frame_ids": [selected]}
    calls = []
    monkeypatch.setattr(frames, "generate_frame", lambda project_id, frame_id, body: (calls.append(frame_id) or {"status": "ready"}))
    assert client.post(root + "/messages", json={"content": "只重做第二张"}).status_code == 200
    assert calls == []
    assert client.post(root + "/confirm").status_code == 200
    assert calls == [selected]
    # Sessions completed by the old implementation can be reopened by a new message.
    with Session(database.get_engine()) as session, session.begin():
        session.get(AgentSession, row["id"]).status = "completed"
    current = {"reply": "可以改人物", "focus_stage": "characters", "action": "reply", "plan_patch": {}}
    continued = client.post(root + "/messages", json={"content": "现在先改人物"})
    assert continued.status_code == 200
    assert continued.json()["stage"] == "characters"


def test_agent_model_failure_keeps_user_message(client, monkeypatch):
    from web.services import agent
    from fastapi import HTTPException
    def fail(state):
        raise HTTPException(502, "模型暂不可用")
    monkeypatch.setattr(agent, "_chat_decision", fail)
    row = client.post("/photodream/api/agent/sessions", json={}).json()
    root = f"/photodream/api/agent/sessions/{row['id']}"
    assert client.post(root + "/messages", json={"content": "这条创意需要保留"}).status_code == 502
    loaded = client.get(root).json()
    assert loaded["messages"][0]["content"] == "这条创意需要保留"
    assert loaded["job"]["status"] == "failed"


def test_agent_plan_edits_and_mode_are_persisted(client):
    created = client.post(
        "/photodream/api/agent/sessions", json={"mode": "guided"}
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    mode = client.post(
        f"/photodream/api/agent/sessions/{session_id}/mode",
        json={"mode": "autonomous"},
    )
    assert mode.status_code == 200
    assert mode.json()["mode"] == "autonomous"

    plan = {
        "title": "雨夜寻相机",
        "brief": "一个人在雨夜寻找旧相机。",
        "tone": "电影感、低饱和",
        "screenplay": "开场：雨夜街头。",
        "characters": [{"name": "林默", "content": "沉默的摄影师。"}],
        "scenes": [{"name": "雨夜街头", "content": "霓虹倒映在积水中。"}],
        "shots": [{"title": "开场", "prompt": "雨夜街头的远景。", "order": 1}],
        "image_settings": {"model": "test-model", "size": "1024x1024"},
    }
    saved = client.post(f"/photodream/api/agent/sessions/{session_id}/plan", json=plan)
    assert saved.status_code == 200
    assert saved.json()["plan"] == plan

    loaded = client.get(f"/photodream/api/agent/sessions/{session_id}")
    assert loaded.status_code == 200
    assert loaded.json()["mode"] == "autonomous"
    assert loaded.json()["plan"]["characters"][0]["name"] == "林默"
def test_multiple_sessions_keep_independent_history_and_delete_only_conversation(client, monkeypatch):
    from web.services import agent

    monkeypatch.setattr(agent, "_chat_decision", lambda state: {
        "reply": "我们继续讨论", "focus_stage": "brief", "action": "reply", "plan_patch": {},
    })
    base = "/photodream/api/agent/sessions"
    first = client.post(base, json={}).json()
    second = client.post(base, json={}).json()
    client.post(f"{base}/{first['id']}/messages", json={"content": "雨夜故事"})
    rows = client.get(base).json()
    assert {row["id"] for row in rows} == {first["id"], second["id"]}
    assert next(row for row in rows if row["id"] == first["id"])["title"] == "雨夜故事"
    assert client.get(f"{base}/{second['id']}").json()["messages"] == []
    assert client.get(f"{base}/{first['id']}").json()["messages"][0]["content"] == "雨夜故事"
    assert client.delete(f"{base}/{first['id']}").status_code == 200
    assert client.get(f"{base}/{first['id']}").status_code == 404
    assert [row["id"] for row in client.get(base).json()] == [second["id"]]


def test_session_delete_is_user_isolated_and_rejects_running_jobs(client, environment):
    from sqlalchemy.orm import Session
    from web.models import AgentSession

    base = "/photodream/api/agent/sessions"
    row = client.post(base, json={}).json()
    with Session(environment[1]) as session, session.begin():
        session.get(AgentSession, row["id"]).job = {"status": "running"}
    assert client.delete(f"{base}/{row['id']}").status_code == 409
    client.post("/photodream/api/auth/logout")
    assert client.post("/photodream/api/auth/register", json={"username": "another_user", "password": "test-password-123"}).status_code == 201
    assert client.get(base).json() == []
    assert client.get(f"{base}/{row['id']}").status_code == 404
    assert client.delete(f"{base}/{row['id']}").status_code == 404


def test_session_project_filter_and_delete_preserve_project(client):
    base = "/photodream/api/agent/sessions"
    project = client.post("/photodream/api/projects", data={"name": "会话项目"}).json()
    row = client.post(base, json={"project_id": project["id"]}).json()
    client.post(base, json={})
    assert [item["id"] for item in client.get(base, params={"project_id": project["id"]}).json()] == [row["id"]]
    assert len(client.get(base, params={"project_id": ""}).json()) == 1
    assert client.delete(f"{base}/{row['id']}").status_code == 200
    assert client.get(f"/photodream/api/projects/{project['id']}").json() == project
