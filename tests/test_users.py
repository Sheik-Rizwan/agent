import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import USER, login


def test_admin_can_create_list_and_delete_users(admin):
    resp = admin.post("/api/v1/users", json={"username": "Bob.Smith", "password": "BobPass12345", "role": "user"})
    assert resp.status_code == 201
    assert resp.json()["username"] == "bob.smith"  # normalized to lowercase
    assert resp.json()["created_by"] == "admin"
    assert "password_hash" not in resp.json()

    names = [u["username"] for u in admin.get("/api/v1/users").json()]
    assert names == ["admin", "bob.smith"]
    assert all("password_hash" not in u for u in admin.get("/api/v1/users").json())

    assert admin.delete("/api/v1/users/bob.smith").json() == {"ok": True}
    assert [u["username"] for u in admin.get("/api/v1/users").json()] == ["admin"]


def test_created_user_can_log_in(admin):
    admin.post("/api/v1/users", json={"username": "carol", "password": "Tr0mbone-xyz"})
    with TestClient(app) as c:
        login(c, {"username": "carol", "password": "Tr0mbone-xyz"})
        assert c.get("/api/v1/auth/me").json()["role"] == "user"  # default role


def test_duplicate_username_conflicts(admin):
    body = {"username": "dave", "password": "Tr0mbone-xyz"}
    assert admin.post("/api/v1/users", json=body).status_code == 201
    assert admin.post("/api/v1/users", json={**body, "username": "DAVE"}).status_code == 409


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 33, "-bob", "bob smith", "bob/../x", "bob@x", "émile", "_bob"],
)
def test_invalid_usernames_rejected(admin, username):
    resp = admin.post("/api/v1/users", json={"username": username, "password": "GoodPass12345"})
    assert resp.status_code == 422, username


@pytest.mark.parametrize(
    "password",
    ["Short1", "nonumbershere", "123456789012", "Has-erin-1234".replace("erin", "frank")],
)
def test_weak_passwords_rejected(admin, password):
    resp = admin.post("/api/v1/users", json={"username": "frank", "password": password})
    assert resp.status_code == 422, password


def test_password_min_length_from_env(admin, monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "16")
    assert admin.post("/api/v1/users", json={"username": "gina", "password": "Tr0mbone-xyz"}).status_code == 422
    assert admin.post("/api/v1/users", json={"username": "gina", "password": "Tr0mbone-xyz-long"}).status_code == 201


def test_password_too_long_rejected(admin):
    assert admin.post("/api/v1/users", json={"username": "hank", "password": "A1" * 65}).status_code == 422


def test_invalid_role_rejected(admin):
    assert admin.post("/api/v1/users", json={"username": "ivan", "password": "IvanPass1234", "role": "root"}).status_code == 422


def test_admin_cannot_delete_self(admin):
    resp = admin.delete("/api/v1/users/admin")
    assert resp.status_code == 403
    assert admin.delete("/api/v1/users/ADMIN").status_code == 403


def test_last_admin_cannot_be_deleted(admin):
    admin.post("/api/v1/users", json={"username": "admin2", "password": "Tr0mbone-abc", "role": "admin"})
    with TestClient(app) as other:
        login(other, {"username": "admin2", "password": "Tr0mbone-abc"})
        assert other.delete("/api/v1/users/admin").status_code == 200  # two admins -> ok
    # admin2 is now the only admin; nobody else can remove it (and it can't remove itself).
    from app.core.config import get_config
    from app.repositories.session_repository import SessionRepository
    from app.repositories.user_repository import UserRepository
    from app.services.user_service import UserService
    from app.core.exceptions import ForbiddenError

    cfg = get_config()
    service = UserService(UserRepository(cfg.data_dir), SessionRepository(cfg.data_dir), cfg)
    with pytest.raises(ForbiddenError, match="last admin"):
        service.delete("admin2", acting_user="someone-else")


def test_delete_unknown_user_404(admin):
    assert admin.delete("/api/v1/users/ghost").status_code == 404
    assert admin.put("/api/v1/users/ghost/password", json={"password": "GhostPass123"}).status_code == 404


def test_admin_reset_password_logs_user_out(admin, user_client):
    resp = admin.put(f"/api/v1/users/{USER['username']}/password", json={"password": "Reset4567890"})
    assert resp.status_code == 200
    assert user_client.get("/api/v1/auth/me").status_code == 401
    with TestClient(app) as c:
        assert c.post("/api/v1/auth/login", json=USER).status_code == 401
        login(c, {"username": USER["username"], "password": "Reset4567890"})


def test_admin_reset_password_validates(admin, user_client):
    assert admin.put(f"/api/v1/users/{USER['username']}/password", json={"password": "weak"}).status_code == 422
    assert user_client.get("/api/v1/auth/me").status_code == 200  # unchanged


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {"username": "evil", "password": "EvilPass1234", "role": "admin"}),
        ("DELETE", "/api/v1/users/admin", None),
        ("PUT", "/api/v1/users/admin/password", {"password": "Hijack123456"}),
        ("PUT", "/api/v1/agent", {"agent_name": "Evil"}),
        ("POST", "/api/v1/agent/reset", None),
    ],
)
def test_regular_user_cannot_use_admin_endpoints(user_client, method, path, body):
    resp = user_client.request(method, path, json=body)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required"


def test_regular_user_cannot_delete_conversations(admin, user_client):
    conv_id = user_client.post("/api/v1/conversations").json()["id"]
    assert user_client.delete(f"/api/v1/conversations/{conv_id}").status_code == 403
    assert admin.delete(f"/api/v1/conversations/{conv_id}").status_code == 200


def test_regular_user_can_use_normal_features(user_client):
    assert user_client.get("/api/v1/agent").status_code == 200
    conv = user_client.post("/api/v1/conversations").json()
    assert user_client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"text": "hi"}).status_code == 200
    assert user_client.get("/api/v1/conversations").json()[0]["created_by"] == USER["username"]
    assert user_client.get("/api/v1/calls/status").status_code == 200
    assert user_client.post(
        "/api/v1/auth/password", json={"current_password": USER["password"], "new_password": "Wonderland99y"}
    ).status_code == 200
