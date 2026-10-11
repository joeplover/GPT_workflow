"""The complete application is served below a shared site's /photodream path."""

from fastapi.testclient import TestClient

from web.app import create_app


def test_prefixed_routes_redirects_cookies_and_docs(environment):
    with TestClient(create_app()) as client:
        assert (
            client.get("/", follow_redirects=False).headers["location"]
            == "/photodream/"
        )
        response = client.get("/photodream?project=example", follow_redirects=False)
        assert response.headers["location"] == "/photodream/?project=example"
        response = client.get(
            "/photodream/storyboard?project=example", follow_redirects=False
        )
        assert (
            response.headers["location"]
            == "/photodream/login?next=%2Fphotodream%2Fstoryboard%3Fproject%3Dexample"
        )
        assert client.get("/photodream/api/projects").status_code == 401
        assert client.get("/photodream/api/health").status_code == 200
        for path in (
            "/api/health",
            "/api/projects",
            "/single",
            "/static/app.js",
            "/outputs/test.png",
        ):
            assert client.get(path).status_code == 404
        response = client.post(
            "/photodream/api/auth/register",
            json={"username": "prefix", "password": "prefix-password-123"},
        )
        assert response.status_code == 201
        assert "Path=/photodream" in response.headers["set-cookie"]
        for path in ("/photodream/", "/photodream/single", "/photodream/storyboard"):
            response = client.get(path)
            assert response.status_code == 200
            assert 'src="/photodream/static/shell.js"' in response.text
        assert (
            'src="/photodream/static/login.js"' in client.get("/photodream/login").text
        )
        docs = client.get("/photodream/docs")
        assert docs.status_code == 200
        assert "/photodream/openapi.json" in docs.text
        assert client.get("/photodream/openapi.json").json()["servers"] == [
            {"url": "/photodream"}
        ]
        response = client.post("/photodream/api/auth/logout")
        assert "Path=/photodream" in response.headers["set-cookie"]
        assert client.get("/photodream/api/auth/me").status_code == 401
