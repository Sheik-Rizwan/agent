import json
import threading

import pytest

from app.core.config import get_config
from app.repositories.conversation_repository import ConversationRepository
from tests.conftest import mock_http


def start(client) -> str:
    resp = client.post("/api/v1/conversations")
    assert resp.status_code == 201
    return resp.json()["id"]


def test_full_conversation_flow(admin):
    conv = admin.post("/api/v1/conversations").json()
    assert conv["greeting"].startswith("Hi, this is Ava")
    assert len(conv["id"]) == 32

    resp = admin.post(f"/api/v1/conversations/{conv['id']}/messages", json={"text": "What are your hours?"})
    assert "Monday to Friday" in resp.json()["reply"]
    assert admin.post(f"/api/v1/conversations/{conv['id']}/end").json() == {"ok": True}

    [summary] = admin.get("/api/v1/conversations").json()
    assert summary["id"] == conv["id"]
    assert summary["channel"] == "web"
    assert summary["created_by"] == "admin"
    assert summary["message_count"] == 3
    assert summary["first_user_message"] == "What are your hours?"
    assert summary["ended_at"] is not None
    assert "messages" not in summary

    detail = admin.get(f"/api/v1/conversations/{conv['id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["assistant", "user", "assistant"]


def test_conversation_is_a_readable_json_file(admin, data_dir):
    conv_id = start(admin)
    admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "hello"})
    data = json.loads((data_dir / "conversations" / f"{conv_id}.json").read_text())
    assert data["id"] == conv_id
    assert [m["content"] for m in data["messages"]][1] == "hello"


def test_cannot_message_an_ended_conversation(admin):
    conv_id = start(admin)
    admin.post(f"/api/v1/conversations/{conv_id}/end")
    resp = admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "hi"})
    assert resp.status_code == 409


def test_ending_twice_keeps_first_end_time(admin):
    conv_id = start(admin)
    admin.post(f"/api/v1/conversations/{conv_id}/end")
    first = admin.get(f"/api/v1/conversations/{conv_id}").json()["ended_at"]
    admin.post(f"/api/v1/conversations/{conv_id}/end")
    assert admin.get(f"/api/v1/conversations/{conv_id}").json()["ended_at"] == first


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_messages_rejected(admin, text):
    conv_id = start(admin)
    assert admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": text}).status_code == 422


def test_message_length_limit(admin):
    conv_id = start(admin)
    assert admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "x" * 2001}).status_code == 422
    assert admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": "x" * 2000}).status_code == 200


def test_unicode_messages_round_trip(admin):
    conv_id = start(admin)
    text = "नमस्ते 👋 — can I book?"
    admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": text})
    assert admin.get(f"/api/v1/conversations/{conv_id}").json()["messages"][1]["content"] == text


@pytest.mark.parametrize(
    "bad_id",
    ["999", "A" * 32, "a" * 31, "a" * 33, "..%2F..%2Fusers", "%2E%2E", "g" * 32, "users"],
)
def test_invalid_or_unknown_ids_are_404(admin, bad_id):
    assert admin.get(f"/api/v1/conversations/{bad_id}").status_code == 404
    assert admin.post(f"/api/v1/conversations/{bad_id}/messages", json={"text": "hi"}).status_code == 404
    assert admin.post(f"/api/v1/conversations/{bad_id}/end").status_code == 404
    assert admin.delete(f"/api/v1/conversations/{bad_id}").status_code == 404


def test_path_traversal_cannot_reach_other_files(admin, data_dir):
    assert ConversationRepository(data_dir).get("../users") is None
    assert ConversationRepository(data_dir).delete("../users") is False
    assert (data_dir / "users.json").exists()


def test_delete_conversation(admin, data_dir):
    conv_id = start(admin)
    assert admin.delete(f"/api/v1/conversations/{conv_id}").json() == {"ok": True}
    assert admin.get(f"/api/v1/conversations/{conv_id}").status_code == 404
    assert not (data_dir / "conversations" / f"{conv_id}.json").exists()
    assert admin.get("/api/v1/conversations").json() == []


def test_list_newest_first_and_limit(admin):
    ids = [start(admin) for _ in range(5)]
    listed = [c["id"] for c in admin.get("/api/v1/conversations").json()]
    assert listed == list(reversed(ids))
    assert len(admin.get("/api/v1/conversations?limit=2").json()) == 2
    assert admin.get("/api/v1/conversations?limit=0").status_code == 422
    assert admin.get("/api/v1/conversations?limit=501").status_code == 422


def test_history_is_trimmed_for_the_llm(admin, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("LLM_MAX_HISTORY_MESSAGES", "3")
    sizes = []

    def handler(request):
        sizes.append(len(json.loads(request.read())["contents"]))
        return __import__("httpx").Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    mock_http(monkeypatch, handler)
    conv_id = start(admin)
    for i in range(4):
        admin.post(f"/api/v1/conversations/{conv_id}/messages", json={"text": f"m{i}"})
    # 3 newest messages; a leading assistant turn is dropped because Gemini needs user first.
    assert all(size <= 3 for size in sizes)
    assert sizes[-1] in (2, 3)


def test_concurrent_messages_are_all_saved(admin, data_dir):
    """Many simultaneous writers on one conversation file: no lost updates."""
    conv_id = start(admin)
    repo = ConversationRepository(get_config().data_dir)
    threads = [threading.Thread(target=repo.add_message, args=(conv_id, "user", f"msg {i}")) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    messages = admin.get(f"/api/v1/conversations/{conv_id}").json()["messages"]
    assert len(messages) == 41
    assert {m["content"] for m in messages[1:]} == {f"msg {i}" for i in range(40)}


def test_message_to_deleted_conversation_does_not_recreate_it(admin, data_dir):
    conv_id = start(admin)
    repo = ConversationRepository(data_dir)
    repo.delete(conv_id)
    assert repo.add_message(conv_id, "user", "late") is False
    assert not (data_dir / "conversations" / f"{conv_id}.json").exists()


def test_corrupt_conversation_file_is_skipped_not_fatal(admin, data_dir):
    good = start(admin)
    bad = start(admin)
    (data_dir / "conversations" / f"{bad}.json").write_text("{not json")
    listed = [c["id"] for c in admin.get("/api/v1/conversations").json()]
    assert listed == [good]
    assert list((data_dir / "conversations").glob(f"{bad}.json.corrupt-*"))


# ---------- agent configuration ----------

def test_agent_fields_come_from_defaults_file(admin):
    agent = admin.get("/api/v1/agent").json()
    assert set(agent) == {
        "agent_name", "greeting", "system_prompt", "phone_not_heard",
        "phone_goodbye_silent", "phone_technical_issue", "phone_unknown_call",
    }


def test_agent_partial_update_and_reset(admin, data_dir):
    defaults = admin.get("/api/v1/agent").json()
    updated = admin.put("/api/v1/agent", json={"agent_name": "Max", "greeting": "Yo!"}).json()
    assert updated["agent_name"] == "Max"
    assert updated["system_prompt"] == defaults["system_prompt"]
    assert json.loads((data_dir / "agent.json").read_text()) == {"agent_name": "Max", "greeting": "Yo!"}
    assert admin.post("/api/v1/conversations").json()["greeting"] == "Yo!"

    assert admin.post("/api/v1/agent/reset").json() == defaults
    assert not (data_dir / "agent.json").exists()


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({}, "Nothing to update"),
        ({"unknown_field": "x"}, "Unknown field"),
        ({"agent_name": ""}, "can't be empty"),
        ({"agent_name": "   "}, "can't be empty"),
        ({"system_prompt": "x" * 20001}, "longer than"),
    ],
)
def test_agent_update_validation(admin, body, fragment):
    resp = admin.put("/api/v1/agent", json=body)
    assert resp.status_code == 422
    assert fragment in json.dumps(resp.json())


@pytest.mark.parametrize("body", [{"agent_name": 5}, {"agent_name": None}, ["agent_name"], "text"])
def test_agent_update_rejects_non_text(admin, body):
    assert admin.put("/api/v1/agent", json=body).status_code == 422


def test_custom_defaults_file_adds_fields(admin, tmp_path, monkeypatch):
    base = json.loads(open(get_config().agent_defaults_path).read())
    base["agent"]["agent_name"] = "Zed"
    base["agent"]["business_hours_note"] = "Closed on public holidays."
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(base))
    monkeypatch.setenv("AGENT_DEFAULTS_PATH", str(custom))

    agent = admin.get("/api/v1/agent").json()
    assert agent["agent_name"] == "Zed"
    assert agent["business_hours_note"] == "Closed on public holidays."
    assert admin.put("/api/v1/agent", json={"business_hours_note": "Open 24/7"}).status_code == 200


@pytest.mark.parametrize(
    "content,fragment",
    [
        (None, "not found"),
        ("{oops", "not valid JSON"),
        ('{"demo": {}}', "\\\"agent\\\" object"),
        ('{"agent": {"agent_name": "x"}}', "missing text for"),
    ],
)
def test_broken_defaults_file_gives_clear_503(admin, tmp_path, monkeypatch, content, fragment):
    path = tmp_path / "defaults.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setenv("AGENT_DEFAULTS_PATH", str(path))
    resp = admin.get("/api/v1/agent")
    assert resp.status_code == 503
    assert fragment in json.dumps(resp.json())
