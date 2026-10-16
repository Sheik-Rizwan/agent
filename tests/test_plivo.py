import base64
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

AUTH_TOKEN = "test-auth-token"
PUBLIC = "https://agent.example.ngrok.app"
WEBHOOKS = "/api/v1/webhooks/plivo"


def sign(path: str, nonce: str = "12345") -> dict:
    digest = hmac.new(AUTH_TOKEN.encode(), (PUBLIC + path + nonce).encode(), hashlib.sha256).digest()
    return {"X-Plivo-Signature-V2": base64.b64encode(digest).decode(), "X-Plivo-Signature-V2-Nonce": nonce}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("PLIVO_AUTH_ID", "MAXXXXXXXXXXXXXXXXXX")
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("PLIVO_PHONE_NUMBER", "+918000000000")
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC)
    monkeypatch.setenv("PLIVO_VALIDATE_SIGNATURE", "true")
    with TestClient(app) as c:
        yield c


def post_webhook(client, path: str, data: dict | None = None):
    """POST to a Plivo webhook with a valid signature (signature ignores the query string)."""
    return client.post(f"{WEBHOOKS}{path}", data=data or {}, headers=sign(f"{WEBHOOKS}{path.split('?')[0]}"))


def parse(resp) -> ET.Element:
    assert resp.headers["content-type"].startswith("application/xml")
    return ET.fromstring(resp.text.split("\n", 1)[1])


def answer_call(client, call_uuid="call-1"):
    resp = post_webhook(client, "/answer", {"CallUUID": call_uuid, "From": "919876543210", "To": "918000000000"})
    assert resp.status_code == 200
    return parse(resp)


def speech_path(xml: ET.Element) -> str:
    action = xml.find("GetInput").get("action")
    assert action.startswith(PUBLIC + WEBHOOKS)
    return action[len(PUBLIC + WEBHOOKS):]


# ---------- REST: placing calls ----------

def test_status_reports_configuration(client):
    status = client.get("/api/v1/calls/status").json()
    assert status == {
        "provider": "plivo",
        "configured": True,
        "phone_number": "+918000000000",
        "public_base_url": PUBLIC,
    }
    assert client.get("/api/v1/health").json()["telephony_configured"] is True


def test_place_call_sends_correct_request_to_plivo(client, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"api_id": "x", "message": "call fired", "request_uuid": "req-123"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    resp = client.post("/api/v1/calls", json={"to": "+91 98765-43210"})
    assert resp.status_code == 201
    assert resp.json() == {"request_uuid": "req-123", "message": "call fired"}

    assert captured["url"] == "https://api.plivo.com/v1/Account/MAXXXXXXXXXXXXXXXXXX/Call/"
    expected_auth = base64.b64encode(f"MAXXXXXXXXXXXXXXXXXX:{AUTH_TOKEN}".encode()).decode()
    assert captured["auth"] == f"Basic {expected_auth}"
    assert captured["body"] == {
        "from": "918000000000",
        "to": "919876543210",
        "answer_url": f"{PUBLIC}{WEBHOOKS}/answer",
        "answer_method": "POST",
        "hangup_url": f"{PUBLIC}{WEBHOOKS}/hangup",
        "hangup_method": "POST",
    }


def test_place_call_rejects_bad_number(client):
    resp = client.post("/api/v1/calls", json={"to": "12-abc-345"})
    assert resp.status_code == 422
    assert "international format" in resp.json()["detail"]


def test_place_call_surfaces_plivo_error(client, monkeypatch):
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "authentication failed"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    resp = client.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 502
    assert "authentication failed" in resp.json()["detail"]


def test_place_call_requires_configuration(client, monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "")
    resp = client.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 503
    assert "PLIVO_AUTH_ID" in resp.json()["detail"]


def test_place_call_requires_public_url(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    resp = client.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 503
    assert "PUBLIC_BASE_URL" in resp.json()["detail"]


# ---------- webhooks: call flow ----------

def test_answer_greets_and_listens(client):
    xml = answer_call(client)
    get_input = xml.find("GetInput")
    assert get_input.get("inputType") == "speech"
    assert get_input.get("method") == "POST"
    assert get_input.find("Speak").text.startswith("Hi, this is Ava")
    # Silence falls through to a redirect back to the same handler.
    assert xml.find("Redirect").text == get_input.get("action")

    convs = client.get("/api/v1/conversations").json()
    assert convs[0]["channel"] == "phone"
    assert convs[0]["call_uuid"] == "call-1"
    assert convs[0]["from_number"] == "919876543210"


def test_answer_retry_reuses_conversation(client):
    answer_call(client, "call-dup")
    answer_call(client, "call-dup")
    assert len(client.get("/api/v1/conversations").json()) == 1


def test_speech_gets_agent_reply(client):
    path = speech_path(answer_call(client))
    xml = parse(post_webhook(client, path, {"Speech": "What are your hours?", "CallUUID": "call-1"}))
    assert "Monday to Friday" in xml.find("GetInput/Speak").text

    conv_id = client.get("/api/v1/conversations").json()[0]["id"]
    messages = client.get(f"/api/v1/conversations/{conv_id}").json()["messages"]
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]
    assert messages[1]["content"] == "What are your hours?"


def test_silence_reprompts_then_hangs_up(client):
    path = speech_path(answer_call(client))
    for _ in range(2):
        xml = parse(post_webhook(client, path))
        assert xml.find("GetInput/Speak").text.startswith("Sorry, I didn't catch that")
        path = speech_path(xml)

    xml = parse(post_webhook(client, path))
    assert xml.find("Hangup") is not None
    assert client.get("/api/v1/conversations").json()[0]["ended_at"] is not None


def test_speech_resets_silence_counter(client):
    path = speech_path(answer_call(client))
    path = speech_path(parse(post_webhook(client, path)))  # one silent turn
    xml = parse(post_webhook(client, path, {"Speech": "hello"}))
    assert "silent=1" in speech_path(xml)


def test_llm_failure_keeps_call_alive(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {"message": "boom"}}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    path = speech_path(answer_call(client))
    xml = parse(post_webhook(client, path, {"Speech": "hello"}))
    assert "technical problem" in xml.find("GetInput/Speak").text


def test_speech_for_unknown_conversation_hangs_up(client):
    xml = parse(post_webhook(client, "/speech?conversation_id=999&silent=1", {"Speech": "hi"}))
    assert xml.find("Hangup") is not None


def test_hangup_ends_conversation(client):
    answer_call(client, "call-end")
    assert post_webhook(client, "/hangup", {"CallUUID": "call-end"}).status_code == 204
    assert client.get("/api/v1/conversations").json()[0]["ended_at"] is not None


def test_xml_escapes_agent_text(client):
    client.put(
        "/api/v1/agent",
        json={"agent_name": "A", "greeting": "Hi <you> & \"friends\"", "system_prompt": "p"},
    )
    xml = answer_call(client)
    assert xml.find("GetInput/Speak").text == 'Hi <you> & "friends"'


# ---------- webhooks: security ----------

def test_webhook_rejects_missing_signature(client):
    resp = client.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"})
    assert resp.status_code == 403


def test_webhook_rejects_wrong_signature(client):
    headers = sign(f"{WEBHOOKS}/answer")
    headers["X-Plivo-Signature-V2"] = "AAAA" + headers["X-Plivo-Signature-V2"][4:]
    resp = client.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"}, headers=headers)
    assert resp.status_code == 403
    assert client.get("/api/v1/conversations").json() == []


def test_signature_check_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("PLIVO_VALIDATE_SIGNATURE", "false")
    resp = client.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"})
    assert resp.status_code == 200
