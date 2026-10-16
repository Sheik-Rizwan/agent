"""Unit tests for config parsing, the JSON store, password hashing, the CLI and Gemini."""

import json
import threading

import httpx
import pytest

from app import cli
from app.core.config import PROJECT_ROOT, get_config
from app.core.exceptions import UpstreamError
from app.core.json_store import JsonFile
from app.core.security import hash_password, verify_password
from app.services.llm_service import LLMService
from tests.conftest import mock_http

# ---------- config ----------


def test_config_defaults():
    cfg = get_config()
    assert cfg.gemini_model == "gemini-2.5-flash"
    assert cfg.gemini_temperature == 0.6
    assert cfg.plivo_validate_signature is True
    assert cfg.cookie_secure is False
    assert cfg.agent_defaults_path == PROJECT_ROOT / "config" / "agent.default.json"


@pytest.mark.parametrize(
    "name,value,attr,expected",
    [
        ("GEMINI_TEMPERATURE", "abc", "gemini_temperature", 0.6),
        ("GEMINI_TEMPERATURE", "5", "gemini_temperature", 0.6),
        ("GEMINI_TEMPERATURE", "1.2", "gemini_temperature", 1.2),
        ("GEMINI_MAX_OUTPUT_TOKENS", "2.5", "gemini_max_output_tokens", 1024),
        ("PLIVO_MAX_SILENT_TURNS", "-1", "plivo_max_silent_turns", 2),
        ("PASSWORD_MIN_LENGTH", "4", "password_min_length", 10),
        ("PLIVO_VALIDATE_SIGNATURE", "maybe", "plivo_validate_signature", True),
        ("PLIVO_VALIDATE_SIGNATURE", "OFF", "plivo_validate_signature", False),
        ("COOKIE_SECURE", "yes", "cookie_secure", True),
        ("PUBLIC_BASE_URL", "https://x.ngrok.app/", "public_base_url", "https://x.ngrok.app"),
    ],
)
def test_config_parsing(monkeypatch, name, value, attr, expected):
    monkeypatch.setenv(name, value)
    assert getattr(get_config(), attr) == expected


def test_relative_data_dir_is_under_project(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "mydata")
    assert get_config().data_dir == PROJECT_ROOT / "mydata"


def test_plivo_configured_needs_all_three(monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "a")
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", "b")
    assert get_config().plivo_configured is False
    monkeypatch.setenv("PLIVO_PHONE_NUMBER", "+918000000000")
    assert get_config().plivo_configured is True


# ---------- JSON store ----------


def test_json_file_roundtrip_and_default(tmp_path):
    f = JsonFile(tmp_path / "x.json", lambda: {"n": 0})
    assert f.read() == {"n": 0}
    with f.update() as data:
        data["n"] += 1
    assert json.loads((tmp_path / "x.json").read_text()) == {"n": 1}


def test_json_update_is_not_saved_when_block_raises(tmp_path):
    f = JsonFile(tmp_path / "x.json", dict)
    f.write({"a": 1})
    with pytest.raises(RuntimeError):
        with f.update() as data:
            data["a"] = 2
            raise RuntimeError("boom")
    assert f.read() == {"a": 1}


def test_json_update_create_false_on_missing_file(tmp_path):
    f = JsonFile(tmp_path / "x.json", dict)
    with f.update(create=False) as data:
        assert data is None
    assert not (tmp_path / "x.json").exists()


def test_json_concurrent_updates_lose_nothing(tmp_path):
    f = JsonFile(tmp_path / "counter.json", lambda: {"n": 0})

    def bump():
        for _ in range(25):
            with f.update() as data:
                data["n"] += 1

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert f.read() == {"n": 200}


def test_json_leaves_no_temp_files(tmp_path):
    f = JsonFile(tmp_path / "x.json", dict)
    for i in range(5):
        f.write({"i": i})
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["x.json"]


def test_json_corrupt_file_is_backed_up(tmp_path, caplog):
    (tmp_path / "x.json").write_text("{broken")
    f = JsonFile(tmp_path / "x.json", lambda: {"fresh": True})
    assert f.read() == {"fresh": True}
    backups = list(tmp_path.glob("x.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{broken"
    assert "Corrupt JSON" in caplog.text


# ---------- passwords ----------


def test_password_hashing():
    stored = hash_password("Correct-Horse-9")
    assert stored.startswith("scrypt$")
    assert verify_password("Correct-Horse-9", stored)
    assert not verify_password("correct-horse-9", stored)
    assert hash_password("Correct-Horse-9") != stored  # random salt


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$AA==$AA==", "scrypt$16384$8$1$!!$!!"])
def test_malformed_hashes_never_verify(stored):
    assert verify_password("anything", stored) is False


# ---------- CLI ----------


def run_cli(monkeypatch, capsys, args, password="Cl1-Password-x"):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": password)
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out + out.err


def test_cli_user_lifecycle(monkeypatch, capsys):
    assert run_cli(monkeypatch, capsys, ["create-user", "boss", "--role", "admin"]) == (0, "Created admin 'boss'\n")
    assert run_cli(monkeypatch, capsys, ["create-user", "worker"])[0] == 0
    code, out = run_cli(monkeypatch, capsys, ["list-users"])
    assert code == 0 and "boss" in out and "worker" in out and "never" in out
    assert run_cli(monkeypatch, capsys, ["reset-password", "worker"], "N3w-Password-x")[0] == 0
    assert run_cli(monkeypatch, capsys, ["delete-user", "worker"])[0] == 0
    code, out = run_cli(monkeypatch, capsys, ["delete-user", "boss"])
    assert code == 1 and "last admin" in out


def test_cli_reports_errors(monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, ["create-user", "x"])
    assert code == 1 and "Username must be" in out
    code, out = run_cli(monkeypatch, capsys, ["create-user", "pat"], password="weak")
    assert code == 1 and "at least" in out
    code, out = run_cli(monkeypatch, capsys, ["delete-user", "ghost"])
    assert code == 1 and "not found" in out


def test_cli_password_mismatch(monkeypatch, capsys):
    answers = iter(["Fir5t-password", "Sec0nd-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-user", "quinn"]) == 1
    assert "don't match" in capsys.readouterr().err


# ---------- Gemini ----------


def gemini(monkeypatch, handler, **env):
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    mock_http(monkeypatch, handler)
    return LLMService(get_config())


async def ask(llm):
    return await llm.generate_reply("Be nice.", [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "Hello"}])


@pytest.mark.anyio
async def test_gemini_request_shape(monkeypatch):
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-goog-api-key"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Hey "}, {"text": "there."}]}}]})

    llm = gemini(monkeypatch, handler, GEMINI_MODEL="gemini-x", GEMINI_TEMPERATURE="0.2", GEMINI_MAX_OUTPUT_TOKENS="99")
    assert await ask(llm) == "Hey there."
    assert seen["key"] == "key"
    assert "/models/gemini-x:generateContent" in seen["url"]
    assert seen["body"]["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]
    assert seen["body"]["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 99}
    assert "Current date and time" in seen["body"]["system_instruction"]["parts"][0]["text"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response,fragment",
    [
        (httpx.Response(400, json={"error": {"message": "API key not valid."}}), "API key not valid"),
        (httpx.Response(500, text="<html>"), "Gemini error 500"),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "SAFETY"),
        (httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}), "MAX_TOKENS"),
        (httpx.Response(200, text="not json"), "unreadable"),
        (httpx.Response(200, json=[1, 2]), "unreadable"),
    ],
)
async def test_gemini_error_paths(monkeypatch, response, fragment):
    llm = gemini(monkeypatch, lambda r: response)
    with pytest.raises(UpstreamError, match=fragment):
        await ask(llm)


@pytest.mark.anyio
async def test_gemini_network_error(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(UpstreamError, match="Could not reach Gemini"):
        await ask(gemini(monkeypatch, handler))


def test_invalid_timezone_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEZONE", "Mars/Olympus")
    assert "server local time" in LLMService(get_config())._clock_context()


def test_demo_replies_come_from_config():
    llm = LLMService(get_config(), {"replies": [{"keywords": ["Pizza"], "reply": "Yum"}], "fallback": "Huh?"})
    assert llm.demo_reply("i want PIZZA") == "Yum"
    assert llm.demo_reply("tacos") == "Huh?"
    assert LLMService(get_config(), {}).demo_reply("x").startswith("Demo mode")
