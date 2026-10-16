import base64
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import httpx
import pytest

from tests.conftest import mock_http

AUTH_TOKEN = "test-auth-token"
PUBLIC = "https://agent.example.ngrok.app"
WEBHOOKS = "/api/v1/webhooks/plivo"


def sign(path: str, nonce: str = "12345") -> dict:
    digest = hmac.new(AUTH_TOKEN.encode(), (PUBLIC + path + nonce).encode(), hashlib.sha256).digest()
    return {"X-Plivo-Signature-V2": base64.b64encode(digest).decode(), "X-Plivo-Signature-V2-Nonce": nonce}


@pytest.fixture(autouse=True)
def plivo_env(monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "MAXXXXXXXXXXXXXXXXXX")
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("PLIVO_PHONE_NUMBER", "+918000000000")
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC)


def post_webhook(client, path: str, data: dict | None = None):
    """POST to a Plivo webhook with a valid signature (the signature ignores the query string)."""
    return client.post(f"{WEBHOOKS}{path}", data=data or {}, headers=sign(f"{WEBHOOKS}{path.split('?')[0]}"))


def parse(resp) -> ET.Element:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")
    return ET.fromstring(resp.text.split("\n", 1)[1])


def answer_call(client, call_uuid="call-1"):
    return parse(post_webhook(client, "/answer", {"CallUUID": call_uuid, "From": "919876543210", "To": "918000000000"}))


def speech_path(xml: ET.Element) -> str:
    action = xml.find("GetInput").get("action")
    assert action.startswith(PUBLIC + WEBHOOKS)
    return action[len(PUBLIC + WEBHOOKS):]


# ---------- REST: placing calls ----------

def test_status_reports_configuration(admin):
    assert admin.get("/api/v1/calls/status").json() == {
        "provider": "plivo",
        "configured": True,
        "phone_number": "+918000000000",
        "public_base_url": PUBLIC,
    }
    assert admin.get("/api/v1/status").json()["telephony_configured"] is True


def test_place_call_sends_correct_request_to_plivo(admin, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"api_id": "x", "message": "call fired", "request_uuid": "req-123"})

    mock_http(monkeypatch, handler)
    resp = admin.post("/api/v1/calls", json={"to": "+91 98765-43210"})
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


def test_regular_user_can_place_calls(user_client, monkeypatch):
    mock_http(monkeypatch, lambda r: httpx.Response(201, json={"message": "call fired", "request_uuid": "r"}))
    assert user_client.post("/api/v1/calls", json={"to": "+919876543210"}).status_code == 201


@pytest.mark.parametrize("number", ["12-abc-345", "+0123456789", "++919876543210", "+91 98765", "1234567890123456"])
def test_place_call_rejects_bad_numbers(admin, number):
    resp = admin.post("/api/v1/calls", json={"to": number})
    assert resp.status_code == 422


def test_place_call_surfaces_plivo_error(admin, monkeypatch):
    mock_http(monkeypatch, lambda r: httpx.Response(401, json={"error": "authentication failed"}))
    resp = admin.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 502
    assert "authentication failed" in resp.json()["detail"]


def test_place_call_handles_non_json_error(admin, monkeypatch):
    mock_http(monkeypatch, lambda r: httpx.Response(500, text="<html>oops</html>"))
    resp = admin.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 502
    assert "Plivo error 500" in resp.json()["detail"]


def test_place_call_handles_network_failure(admin, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    mock_http(monkeypatch, handler)
    resp = admin.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 502
    assert "Could not reach Plivo" in resp.json()["detail"]


def test_place_call_requires_configuration(admin, monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "")
    resp = admin.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 503
    assert "PLIVO_AUTH_ID" in resp.json()["detail"]


def test_place_call_requires_public_url(admin, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    resp = admin.post("/api/v1/calls", json={"to": "+919876543210"})
    assert resp.status_code == 503
    assert "PUBLIC_BASE_URL" in resp.json()["detail"]


# ---------- webhooks: call flow ----------

def test_answer_greets_and_listens(admin):
    xml = answer_call(admin)
    get_input = xml.find("GetInput")
    assert get_input.get("inputType") == "speech"
    assert get_input.get("method") == "POST"
    assert get_input.find("Speak").text.startswith("Hi, this is Ava")
    assert xml.find("Redirect").text == get_input.get("action")  # silence falls through to here

    [conv] = admin.get("/api/v1/conversations").json()
    assert conv["channel"] == "phone"
    assert conv["call_uuid"] == "call-1"
    assert conv["from_number"] == "919876543210"
    assert conv["created_by"] is None


def test_voice_and_language_from_env(admin, monkeypatch):
    monkeypatch.setenv("PLIVO_VOICE", "MAN")
    monkeypatch.setenv("PLIVO_LANGUAGE", "en-GB")
    xml = answer_call(admin)
    assert xml.find("GetInput").get("language") == "en-GB"
    assert xml.find("GetInput/Speak").get("voice") == "MAN"


def test_answer_works_with_get(admin):
    resp = admin.get(f"{WEBHOOKS}/answer?CallUUID=get-1", headers=sign(f"{WEBHOOKS}/answer"))
    assert parse(resp).find("GetInput") is not None


def test_answer_retry_reuses_conversation(admin):
    answer_call(admin, "call-dup")
    answer_call(admin, "call-dup")
    assert len(admin.get("/api/v1/conversations").json()) == 1


def test_answer_without_call_uuid_still_works(admin):
    parse(post_webhook(admin, "/answer", {}))
    assert len(admin.get("/api/v1/conversations").json()) == 1


def test_speech_gets_agent_reply(admin):
    path = speech_path(answer_call(admin))
    xml = parse(post_webhook(admin, path, {"Speech": "What are your hours?", "CallUUID": "call-1"}))
    assert "Monday to Friday" in xml.find("GetInput/Speak").text
    conv_id = admin.get("/api/v1/conversations").json()[0]["id"]
    messages = admin.get(f"/api/v1/conversations/{conv_id}").json()["messages"]
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]


def test_silence_reprompts_then_hangs_up(admin):
    path = speech_path(answer_call(admin))
    for _ in range(2):
        xml = parse(post_webhook(admin, path))
        assert xml.find("GetInput/Speak").text.startswith("Sorry, I didn't catch that")
        path = speech_path(xml)
    xml = parse(post_webhook(admin, path))
    assert xml.find("Hangup") is not None
    assert admin.get("/api/v1/conversations").json()[0]["ended_at"] is not None


@pytest.mark.parametrize("limit", ["0", "1", "4"])
def test_silence_limit_from_env(admin, monkeypatch, limit):
    monkeypatch.setenv("PLIVO_MAX_SILENT_TURNS", limit)
    path = speech_path(answer_call(admin))
    for _ in range(int(limit)):
        xml = parse(post_webhook(admin, path))
        path = speech_path(xml)
    assert parse(post_webhook(admin, path)).find("Hangup") is not None


def test_phone_messages_come_from_agent_config(admin):
    admin.put("/api/v1/agent", json={"phone_not_heard": "Pardon?", "phone_goodbye_silent": "Bye now."})
    path = speech_path(answer_call(admin))
    xml = parse(post_webhook(admin, path))
    assert xml.find("GetInput/Speak").text == "Pardon?"


def test_speech_resets_silence_counter(admin):
    path = speech_path(answer_call(admin))
    path = speech_path(parse(post_webhook(admin, path)))  # one silent turn
    xml = parse(post_webhook(admin, path, {"Speech": "hello"}))
    assert "silent=1" in speech_path(xml)


def test_llm_failure_keeps_call_alive(admin, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    mock_http(monkeypatch, lambda r: httpx.Response(500, json={"error": {"message": "boom"}}))
    path = speech_path(answer_call(admin))
    xml = parse(post_webhook(admin, path, {"Speech": "hello"}))
    assert "technical problem" in xml.find("GetInput/Speak").text


@pytest.mark.parametrize("conv_id", ["f" * 32, "not-an-id", "..%2Fusers"])
def test_speech_for_unknown_conversation_hangs_up(admin, conv_id):
    xml = parse(post_webhook(admin, f"/speech?conversation_id={conv_id}&silent=1", {"Speech": "hi"}))
    assert xml.find("Hangup") is not None


def test_speech_with_bad_silent_param_is_422(admin):
    path = speech_path(answer_call(admin)).replace("silent=1", "silent=abc")
    assert post_webhook(admin, path).status_code == 422


def test_hangup_ends_conversation(admin):
    answer_call(admin, "call-end")
    assert post_webhook(admin, "/hangup", {"CallUUID": "call-end"}).status_code == 204
    assert admin.get("/api/v1/conversations").json()[0]["ended_at"] is not None


def test_hangup_for_unknown_call_is_harmless(admin):
    assert post_webhook(admin, "/hangup", {"CallUUID": "nope"}).status_code == 204
    assert post_webhook(admin, "/hangup", {}).status_code == 204


def test_deleting_phone_conversation_cleans_call_index(admin, data_dir):
    answer_call(admin, "call-del")
    conv_id = admin.get("/api/v1/conversations").json()[0]["id"]
    admin.delete(f"/api/v1/conversations/{conv_id}")
    assert "call-del" not in json.loads((data_dir / "call_index.json").read_text())
    answer_call(admin, "call-del")  # a retried answer now starts fresh instead of crashing
    assert len(admin.get("/api/v1/conversations").json()) == 1


def test_xml_escapes_agent_text(admin):
    admin.put("/api/v1/agent", json={"greeting": "Hi <you> & \"friends\" ]]>"})
    xml = answer_call(admin)
    assert xml.find("GetInput/Speak").text == 'Hi <you> & "friends" ]]>'


# ---------- webhooks: security ----------

def test_webhooks_do_not_need_login_but_need_signature(anon):
    assert post_webhook(anon, "/answer", {"CallUUID": "x"}).status_code == 200
    assert anon.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"}).status_code == 403


def test_webhook_rejects_wrong_signature(admin):
    headers = sign(f"{WEBHOOKS}/answer")
    headers["X-Plivo-Signature-V2"] = "AAAA" + headers["X-Plivo-Signature-V2"][4:]
    assert admin.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"}, headers=headers).status_code == 403
    assert admin.get("/api/v1/conversations").json() == []


def test_signature_for_another_path_is_rejected(admin):
    resp = admin.post(f"{WEBHOOKS}/answer", data={}, headers=sign(f"{WEBHOOKS}/hangup"))
    assert resp.status_code == 403


def test_webhook_without_plivo_token_is_503(admin, monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", "")
    assert admin.post(f"{WEBHOOKS}/answer", data={}).status_code == 503


def test_signature_check_can_be_disabled(admin, monkeypatch):
    monkeypatch.setenv("PLIVO_VALIDATE_SIGNATURE", "false")
    assert admin.post(f"{WEBHOOKS}/answer", data={"CallUUID": "x"}).status_code == 200
