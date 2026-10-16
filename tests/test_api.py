import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    for var in ("GEMINI_API_KEY", "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER", "PUBLIC_BASE_URL"):
        monkeypatch.setenv(var, "")
    with TestClient(app) as c:
        yield c


def test_health_reports_demo_mode_without_key(client):
    health = client.get("/api/v1/health").json()
    assert health["mode"] == "demo"
    assert health["telephony_configured"] is False


def test_index_and_static_assets_are_served(client):
    assert "<title>" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_settings_roundtrip_and_reset(client):
    defaults = client.get("/api/v1/agent").json()
    assert defaults["agent_name"] == "Ava"

    body = {"agent_name": "Max", "greeting": "Hello there", "system_prompt": "Be brief."}
    assert client.put("/api/v1/agent", json=body).json() == body
    assert client.get("/api/v1/agent").json() == body

    assert client.post("/api/v1/agent/reset").json() == defaults


def test_settings_validation(client):
    resp = client.put("/api/v1/agent", json={"agent_name": "", "greeting": "x", "system_prompt": "y"})
    assert resp.status_code == 422


def test_conversation_flow_in_demo_mode(client):
    conv = client.post("/api/v1/conversations").json()
    assert conv["greeting"].startswith("Hi, this is Ava")

    resp = client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"text": "What are your hours?"})
    assert resp.status_code == 200
    assert "Monday to Friday" in resp.json()["reply"]

    assert client.post(f"/api/v1/conversations/{conv['id']}/end").json() == {"ok": True}

    listed = client.get("/api/v1/conversations").json()
    assert listed[0]["id"] == conv["id"]
    assert listed[0]["message_count"] == 3
    assert listed[0]["first_user_message"] == "What are your hours?"
    assert listed[0]["ended_at"] is not None
    assert listed[0]["channel"] == "web"

    messages = client.get(f"/api/v1/conversations/{conv['id']}").json()["messages"]
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]


def test_new_greeting_used_after_settings_change(client):
    client.put("/api/v1/agent", json={"agent_name": "Max", "greeting": "Yo!", "system_prompt": "p"})
    assert client.post("/api/v1/conversations").json()["greeting"] == "Yo!"


def test_blank_message_rejected(client):
    conv_id = client.post("/api/v1/conversations").json()["id"]
    assert client.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "   "}).status_code == 422
    assert client.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": ""}).status_code == 422


def test_unknown_conversation_returns_404(client):
    assert client.get("/api/v1/conversations/999").status_code == 404
    assert client.post("/api/v1/conversations/999/messages", json={"text": "hi"}).status_code == 404
    assert client.post("/api/v1/conversations/999/end").status_code == 404
    assert client.delete("/api/v1/conversations/999").status_code == 404


def test_delete_conversation_removes_messages(client):
    conv_id = client.post("/api/v1/conversations").json()["id"]
    assert client.delete(f"/api/v1/conversations/{conv_id}").json() == {"ok": True}
    assert client.get(f"/api/v1/conversations/{conv_id}").status_code == 404
    assert client.get("/api/v1/conversations").json() == []


def test_gemini_request_shape_and_parsing(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.read()
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Sure thing."}]}}]})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    conv_id = client.post("/api/v1/conversations").json()["id"]
    resp = client.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "Book me in"})
    assert resp.json() == {"reply": "Sure thing."}
    assert captured["headers"]["x-goog-api-key"] == "test-key"

    import json
    body = json.loads(captured["body"])
    # The greeting is a model turn before any user turn, so it is dropped for Gemini.
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Book me in"}]}]
    assert "Current date and time" in body["system_instruction"]["parts"][0]["text"]


def test_gemini_error_becomes_502(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "bad-key")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": {"message": "API key not valid."}})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    conv_id = client.post("/api/v1/conversations").json()["id"]
    resp = client.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "hi"})
    assert resp.status_code == 502
    assert "API key not valid" in resp.json()["detail"]
