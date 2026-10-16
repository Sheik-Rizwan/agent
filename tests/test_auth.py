import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import ADMIN, USER, login

PROTECTED = [
    ("GET", "/api/v1/status"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/password"),
    ("GET", "/api/v1/agent"),
    ("PUT", "/api/v1/agent"),
    ("POST", "/api/v1/agent/reset"),
    ("GET", "/api/v1/conversations"),
    ("POST", "/api/v1/conversations"),
    ("GET", "/api/v1/conversations/" + "a" * 32),
    ("DELETE", "/api/v1/conversations/" + "a" * 32),
    ("POST", "/api/v1/conversations/" + "a" * 32 + "/messages"),
    ("POST", "/api/v1/conversations/" + "a" * 32 + "/end"),
    ("GET", "/api/v1/calls/status"),
    ("POST", "/api/v1/calls"),
    ("GET", "/api/v1/users"),
    ("POST", "/api/v1/users"),
    ("DELETE", "/api/v1/users/admin"),
    ("PUT", "/api/v1/users/admin/password"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_protected_endpoint_requires_login(anon, method, path):
    resp = anon.request(method, path, json={})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


def test_public_endpoints(anon):
    assert anon.get("/api/v1/health").json() == {"status": "ok"}
    assert anon.get("/api/v1/auth/setup-status").json() == {"has_users": True}
    assert anon.get("/").status_code == 200


def test_setup_status_without_users(monkeypatch):
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/setup-status").json() == {"has_users": False}


def test_login_sets_secure_cookie_and_returns_user(anon):
    resp = anon.post("/api/v1/auth/login", json=ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert "password_hash" not in body["user"]
    assert body["user"]["last_login_at"] is not None
    assert body["expires_in"] == 12 * 3600
    cookie = resp.headers["set-cookie"].lower()
    assert "agent_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" not in cookie.replace("samesite", "")


def test_cookie_secure_flag_from_env(anon, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    resp = anon.post("/api/v1/auth/login", json=ADMIN)
    assert "; secure" in resp.headers["set-cookie"].lower()


def test_login_is_case_insensitive_for_username(anon):
    assert anon.post("/api/v1/auth/login", json={"username": "  ADMIN ", "password": ADMIN["password"]}).status_code == 200


@pytest.mark.parametrize(
    "creds",
    [
        {"username": "admin", "password": "wrong-password1"},
        {"username": "nobody", "password": ADMIN["password"]},
        {"username": "admin", "password": ADMIN["password"].lower()},
    ],
)
def test_bad_credentials_get_generic_401(anon, creds):
    resp = anon.post("/api/v1/auth/login", json=creds)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


@pytest.mark.parametrize("body", [{}, {"username": "admin"}, {"username": "", "password": "x"}, {"username": "a" * 65, "password": "x"}])
def test_login_validation(anon, body):
    assert anon.post("/api/v1/auth/login", json=body).status_code == 422


def test_bearer_token_works_without_cookie(anon):
    token = login(anon, ADMIN)
    with TestClient(app) as other:
        assert other.get("/api/v1/auth/me").status_code == 401
        resp = other.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"
        assert other.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}x"}).status_code == 401
        assert other.get("/api/v1/auth/me", headers={"Authorization": "Bearer "}).status_code == 401


def test_logout_invalidates_session(anon):
    token = login(anon, ADMIN)
    assert anon.post("/api/v1/auth/logout").json() == {"ok": True}
    assert anon.get("/api/v1/auth/me").status_code == 401
    # The old token is dead too, not just the cookie.
    assert anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_without_session_is_harmless(anon):
    assert anon.post("/api/v1/auth/logout").status_code == 200


def test_expired_session_is_rejected_and_removed(anon, data_dir):
    token = login(anon, ADMIN)
    sessions_file = data_dir / "sessions.json"
    data = json.loads(sessions_file.read_text())
    for session in data["sessions"].values():
        session["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    sessions_file.write_text(json.dumps(data))

    resp = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"]
    assert json.loads(sessions_file.read_text())["sessions"] == {}


def test_session_ttl_from_env(anon, monkeypatch):
    monkeypatch.setenv("SESSION_TTL_HOURS", "0.5")
    assert anon.post("/api/v1/auth/login", json=ADMIN).json()["expires_in"] == 1800


def test_tokens_are_stored_hashed(anon, data_dir):
    token = login(anon, ADMIN)
    raw = (data_dir / "sessions.json").read_text()
    assert token not in raw
    assert ADMIN["password"] not in (data_dir / "users.json").read_text()


def test_rate_limit_locks_out_after_failures(anon, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    bad = {"username": "admin", "password": "nope-nope-1"}
    for _ in range(3):
        assert anon.post("/api/v1/auth/login", json=bad).status_code == 401
    resp = anon.post("/api/v1/auth/login", json=ADMIN)  # even the right password
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    # Other usernames from the same client are not locked.
    assert anon.post("/api/v1/auth/login", json={"username": "x", "password": "y"}).status_code == 401


def test_successful_login_resets_failure_count(anon, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    bad = {"username": "admin", "password": "nope-nope-1"}
    for _ in range(2):
        anon.post("/api/v1/auth/login", json=bad)
    login(anon, ADMIN)
    for _ in range(2):
        assert anon.post("/api/v1/auth/login", json=bad).status_code == 401
    assert anon.post("/api/v1/auth/login", json=ADMIN).status_code == 200


def test_lockout_expires(anon, monkeypatch):
    import app.services.auth_service as auth_module

    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", "1")
    now = [1000.0]
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now[0])
    anon.post("/api/v1/auth/login", json={"username": "admin", "password": "nope-nope-1"})
    assert anon.post("/api/v1/auth/login", json=ADMIN).status_code == 429
    now[0] += 61
    assert anon.post("/api/v1/auth/login", json=ADMIN).status_code == 200


# ---------- change own password ----------

def test_change_password_flow(admin):
    with TestClient(app) as elsewhere:
        token_other = login(elsewhere, ADMIN)  # a second session on another device
    resp = admin.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN["password"], "new_password": "BrandNew4567"},
    )
    assert resp.status_code == 200
    assert admin.get("/api/v1/auth/me").status_code == 200  # this session survives
    assert admin.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_other}"}).status_code == 401
    admin.post("/api/v1/auth/logout")
    assert admin.post("/api/v1/auth/login", json=ADMIN).status_code == 401
    assert admin.post("/api/v1/auth/login", json={"username": "admin", "password": "BrandNew4567"}).status_code == 200


@pytest.mark.parametrize(
    "current,new,status",
    [
        ("wrong-pass-1", "BrandNew4567", 401),
        (ADMIN["password"], ADMIN["password"], 422),
        (ADMIN["password"], "short1", 422),
        (ADMIN["password"], "lettersonlyhere", 422),
        (ADMIN["password"], "1234567890123", 422),
        (ADMIN["password"], "Myadminpassword1", 422),
    ],
)
def test_change_password_rejections(admin, current, new, status):
    resp = admin.post("/api/v1/auth/password", json={"current_password": current, "new_password": new})
    assert resp.status_code == status, resp.text
    assert admin.get("/api/v1/auth/me").status_code == 200  # still logged in


# ---------- bootstrap ----------

def test_bootstrap_admin_only_when_no_users(monkeypatch, data_dir):
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    monkeypatch.setenv("ADMIN_PASSWORD", "Tr0mbone-xyz9")
    with TestClient(app):
        pass
    monkeypatch.setenv("ADMIN_USERNAME", "second")
    monkeypatch.setenv("ADMIN_PASSWORD", "Tr0mbone-qrs1")
    with TestClient(app):
        pass
    users = json.loads((data_dir / "users.json").read_text())["users"]
    assert list(users) == ["root"]


def test_bootstrap_with_weak_password_creates_nobody(monkeypatch, caplog):
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    monkeypatch.setenv("ADMIN_PASSWORD", "weak")
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/setup-status").json() == {"has_users": False}
    assert "Could not create the admin" in caplog.text


def test_deleted_user_session_stops_working(admin, user_client):
    assert user_client.get("/api/v1/auth/me").status_code == 200
    assert admin.delete(f"/api/v1/users/{USER['username']}").status_code == 200
    assert user_client.get("/api/v1/auth/me").status_code == 401
