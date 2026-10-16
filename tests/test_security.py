import os
import stat

import pytest


def test_security_headers_on_pages_and_api(anon):
    for path in ("/", "/api/v1/health", "/static/app.js"):
        headers = anon.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "script-src 'self'" in headers["content-security-policy"]


def test_api_responses_are_not_cached(admin):
    assert admin.get("/api/v1/auth/me").headers["cache-control"] == "no-store"
    assert "cache-control" not in admin.get("/static/app.js").headers or \
        admin.get("/static/app.js").headers["cache-control"] != "no-store"


def test_docs_page_works_without_csp(anon):
    resp = anon.get("/docs")
    assert resp.status_code == 200
    assert "content-security-policy" not in resp.headers


def test_errors_also_get_security_headers(anon):
    resp = anon.get("/api/v1/agent")
    assert resp.status_code == 401
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://testserver.evil.example", "null"])
def test_cross_origin_writes_are_blocked(admin, origin):
    resp = admin.post("/api/v1/conversations", headers={"Origin": origin})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-origin request blocked"
    resp = admin.post("/api/v1/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": origin})
    assert resp.status_code == 403


def test_same_origin_and_no_origin_writes_are_allowed(admin):
    assert admin.post("/api/v1/conversations", headers={"Origin": "http://testserver"}).status_code == 201
    assert admin.post("/api/v1/conversations").status_code == 201


def test_cross_origin_reads_are_not_blocked_by_origin_check(admin):
    # GETs don't change state; the session cookie is SameSite=Strict anyway.
    assert admin.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200


def test_data_files_are_private(admin, data_dir):
    admin.post("/api/v1/conversations")
    admin.put("/api/v1/agent", json={"agent_name": "Max"})
    files = list(data_dir.rglob("*.json"))
    assert {f.name for f in files} >= {"users.json", "sessions.json", "agent.json"}
    for f in files:
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700


def test_no_secrets_in_user_responses(admin):
    for path in ("/api/v1/auth/me", "/api/v1/users"):
        assert "password_hash" not in admin.get(path).text
        assert "session_hash" not in admin.get(path).text


def test_unknown_routes_404(admin):
    assert admin.get("/api/v1/nope").status_code == 404
    assert admin.get("/static/../app/main.py").status_code == 404
