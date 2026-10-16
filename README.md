# Agent: a voice AI receptionist for the browser and the phone

Talk to an AI receptionist out loud, in your browser or on a **real phone call**. It listens, thinks with **Google Gemini**, and answers in a natural voice. Phone calls go through **Plivo**. Everything is behind a login with admin and user roles, all data is stored as readable **JSON files**, and the agent's persona is edited from the UI or a JSON file, not hardcoded.

![Agent UI: a voice call with Ava, the dental clinic receptionist](docs/screenshot.png)

## Features

- **Voice conversation in the browser.** Speech-to-text and text-to-speech run in the browser. Hands-free mode starts listening again after each reply, and you can interrupt the agent mid-sentence.
- **Phone calls through Plivo.** Start outbound calls from the UI or REST API, and receive inbound calls on your Plivo number. Silence is handled politely, and every call is transcribed.
- **Gemini-powered,** with an offline **demo mode** when there's no API key.
- **Login and roles.** Admins manage users and the agent; users talk, call and view history. Admins can create users, delete them and reset passwords, and everyone can change their own password.
- **Secure by default.** scrypt password hashing, hashed session tokens, HttpOnly SameSite cookies, login rate limiting, CSRF Origin checks, a strict Content-Security-Policy, signed Plivo webhooks, and private (0600) data files.
- **JSON storage.** One file per conversation, plus `users.json`, `sessions.json` and `agent.json`. Writes are atomic and locked so nothing is lost.
- **Nothing hardcoded.** Persona, phone phrases and demo replies live in `config/agent.default.json`; every limit and timeout is an environment variable.
- **Clean architecture.** Controllers → services → repositories, a versioned REST API (`/api/v1`) and interactive docs at `/docs`.
- **Well tested.** 209 automated tests, plus an end-to-end browser test.

## Documentation

| Read this | For |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, folders, JSON data files, security model, roles |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | Step-by-step flows (web call, phone call, login, lifecycle, users, agent config), each step mapped to the code |
| [docs/diagrams/](docs/diagrams/) | Interactive diagrams: open the `.html` files in a browser |
| http://localhost:8000/docs | Live REST API reference (when the server is running) |

Each Python module also starts with a docstring explaining what it does and why.

## Quick start

Requires Python 3.10+. For browser voice input, use Chrome or Edge.

```bash
git clone https://github.com/Sheik-Rizwan/agent.git
cd agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in what you need (see below)
python -m app.cli create-user admin --role admin    # first admin; prompts for a password
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 and sign in. Press **Start call** and allow microphone access.

You can also create the first admin by setting `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`. They're used only when no users exist yet.

Get a free Gemini API key at https://aistudio.google.com/apikey and put it in `GEMINI_API_KEY`. Without one, the app runs in demo mode.

## Phone calls with Plivo

1. Create a Plivo account. Copy the **Auth ID** and **Auth Token**, and get a voice-enabled number.
2. Expose your server: `ngrok http 8000`.
3. In `.env`, set `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER`, `PUBLIC_BASE_URL` (your ngrok URL) and `COOKIE_SECURE=true`. Restart the server.
4. **Outbound:** use **Call a phone** in the UI, or:
   ```bash
   curl -X POST http://localhost:8000/api/v1/calls \
        -H "Authorization: Bearer <token from /api/v1/auth/login>" \
        -H "Content-Type: application/json" -d '{"to": "+919876543210"}'
   ```
5. **Inbound (optional):** in the Plivo console, create a Voice Application. Set the answer URL to `<PUBLIC_BASE_URL>/api/v1/webhooks/plivo/answer` and the hangup URL to `<PUBLIC_BASE_URL>/api/v1/webhooks/plivo/hangup` (both POST), then assign it to your number.

> Trial Plivo accounts can only call numbers verified in the console.

## Configuration

Every setting is an environment variable with a sensible default. The full list, with comments, is in [`.env.example`](.env.example).

| Group | Variables |
|---|---|
| First admin | `ADMIN_USERNAME`, `ADMIN_PASSWORD` |
| Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TEMPERATURE`, `GEMINI_MAX_OUTPUT_TOKENS`, `GEMINI_TIMEOUT_SECONDS`, `LLM_MAX_HISTORY_MESSAGES`, `AGENT_TIMEZONE` |
| Plivo | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER`, `PUBLIC_BASE_URL`, `PLIVO_VOICE`, `PLIVO_LANGUAGE`, `PLIVO_MAX_SILENT_TURNS`, `PLIVO_VALIDATE_SIGNATURE` |
| Security | `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `PASSWORD_MIN_LENGTH`, `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`, `ENABLE_API_DOCS` |
| Storage | `DATA_DIR`, `AGENT_DEFAULTS_PATH` |

The agent's persona, greeting, prompt, phone phrases and demo replies are in [`config/agent.default.json`](config/agent.default.json). Admins can override any of them in the **Agent** tab.

## REST API (`/api/v1`)

Log in with `POST /auth/login`. The browser gets an HttpOnly cookie; API clients send `Authorization: Bearer <token>`.

| Method | Path | Who | Description |
|---|---|---|---|
| GET | `/health` | public | Liveness probe |
| GET | `/auth/setup-status` | public | Whether any users exist |
| POST | `/auth/login` | public | Log in and get a session |
| POST | `/auth/logout` | any | End the session |
| GET | `/auth/me` | user | The current user |
| POST | `/auth/password` | user | Change your own password |
| GET | `/status` | user | LLM mode, model and telephony status |
| GET | `/agent` | user | Agent configuration |
| PUT | `/agent` | admin | Update any agent fields |
| POST | `/agent/reset` | admin | Restore the defaults |
| GET / POST | `/conversations` | user | List conversations, or start a web conversation |
| GET | `/conversations/{id}` | user | Transcript |
| DELETE | `/conversations/{id}` | admin | Delete a conversation |
| POST | `/conversations/{id}/messages` | user | Send a message and get the reply |
| POST | `/conversations/{id}/end` | user | End a conversation |
| GET | `/calls/status` | user | Plivo configuration |
| POST | `/calls` | user | Place a phone call |
| GET / POST | `/users` | admin | List or create users |
| DELETE | `/users/{username}` | admin | Delete a user |
| PUT | `/users/{username}/password` | admin | Reset a user's password |
| POST | `/webhooks/plivo/answer`, `/speech`, `/hangup` | Plivo signature | Call control |

Errors are JSON `{"detail": "..."}` with status `401` (not logged in), `403` (forbidden), `404`, `409` (conflict), `422` (invalid input), `429` (too many logins), `502` (Gemini or Plivo failed) or `503` (not configured).

## Tests

```bash
pip install -r requirements-dev.txt
pytest          # 209 tests, about 25 seconds, no keys or network needed
ruff check app tests
```

The tests cover:
- authentication: login, logout, expiry, Bearer tokens, rate limiting and lockout expiry
- permissions on every endpoint, and user management edge cases
- conversation edge cases: invalid ids, path traversal, concurrency, corrupt files, ended conversations
- agent config validation and custom defaults files
- the full Plivo call flow and signature checks
- security headers and CSRF
- config parsing, the JSON store, the CLI, and Gemini error paths

Gemini and Plivo are replaced by a mock HTTP transport.

## Roadmap

- Lower latency with Plivo audio streaming and the Gemini Live API
- Tool calling for real calendar booking (Google Calendar)
- Call recording and post-call summaries
