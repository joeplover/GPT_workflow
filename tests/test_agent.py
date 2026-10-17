def test_agent_conversation_and_stage_materialization(client):
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
    assert session["plan"]["characters"]
    assert session["stage"] == "brief"

    confirmed = client.post(
        f"/photodream/api/agent/sessions/{session['id']}/confirm"
    )
    assert confirmed.status_code == 200
    session = confirmed.json()
    assert session["project_id"]
    assert session["stage"] == "script"

    for _ in range(5):
        session = client.post(
            f"/photodream/api/agent/sessions/{session['id']}/confirm"
        ).json()
    project = client.get(f"/photodream/api/projects/{session['project_id']}").json()
    assert project["screenplay_text"]
    assert project["character_cards"]
    assert project["scene_cards"]
    assert project["frames"]
    assert all(frame["marked"] for frame in project["frames"])


def test_agent_sessions_are_project_scoped(client):
    first = client.post(
        "/photodream/api/agent/sessions", json={"project_id": "missing"}
    )
    assert first.status_code == 404


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
