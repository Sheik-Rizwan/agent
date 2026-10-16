# Agent: a voice AI receptionist for the browser and the phone

Talk to an AI receptionist out loud, in your browser or on a **real phone call**. It listens, thinks with **Google Gemini**, and answers in a natural voice. Phone calls go through **Plivo**. You can change the agent's personality and knowledge from the UI, and every conversation is saved with a full transcript.

![Agent UI: a voice call with Ava, the dental clinic receptionist](docs/screenshot.png)

The default persona is **Ava**, the receptionist for a dental clinic. She answers questions about hours, prices and location, books appointments, and handles emergencies safely. To turn her into a receptionist for any other business, edit the prompt in the **Agent** tab.

## Features

- **Voice conversation in the browser.** Speech-to-text and text-to-speech run in the browser. Hands-free mode starts listening again after each reply, and you can interrupt the agent mid-sentence.
- **Phone calls through Plivo.** Start an outbound call from the UI or the REST API, or let people call your Plivo number. Callers can talk over the agent, silence is handled politely, and every call is transcribed into History.
- **Gemini-powered.** Uses the Gemini API (`gemini-2.5-flash` by default). The current date and time are added to every request, so the agent can say things like "tomorrow" correctly.
- **Configurable agent.** Change the name, greeting, system prompt and voice without touching code.
- **Versioned REST API.** Everything lives under `/api/v1`, with interactive docs at `/docs`.
- **Layered architecture.** Controllers, services and repositories are separate, so each layer can be tested and swapped on its own.
- **Demo mode.** With no API key, the agent gives canned replies, so the UI can be shown anywhere.
- **Secure webhooks.** Plivo requests are checked against their HMAC signature.

## Quick start

Requires Python 3.10+. For browser voice input, use Chrome or Edge.

```bash
git clone https://github.com/Sheik-Rizwan/agent.git
cd agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in your keys
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, press **Start call**, and allow microphone access. The API docs are at http://localhost:8000/docs.

Get a free Gemini API key at https://aistudio.google.com/apikey. If you leave `GEMINI_API_KEY` empty, the app runs in demo mode.

## Phone calls with Plivo

1. **Get credentials.** Create a Plivo account and copy the **Auth ID** and **Auth Token** from the console dashboard. Buy or rent a voice-enabled number.
2. **Expose the server.** Plivo has to reach your server over the internet. For local development:
   ```bash
   ngrok http 8000
   ```
3. **Fill in `.env`:**
   ```env
   PLIVO_AUTH_ID=MAxxxxxxxxxxxxxxxxxx
   PLIVO_AUTH_TOKEN=your-auth-token
   PLIVO_PHONE_NUMBER=+918000000000
   PUBLIC_BASE_URL=https://your-subdomain.ngrok-free.app
   ```
   Restart the server after changing `.env`.
4. **Outbound calls.** Enter a number in **Call a phone** in the UI, or:
   ```bash
   curl -X POST http://localhost:8000/api/v1/calls \
        -H "Content-Type: application/json" -d '{"to": "+919876543210"}'
   ```
5. **Inbound calls (optional).** In the Plivo console, create a Voice **Application** with:
   - Answer URL: `https://<PUBLIC_BASE_URL>/api/v1/webhooks/plivo/answer` (method POST)
   - Hangup URL: `https://<PUBLIC_BASE_URL>/api/v1/webhooks/plivo/hangup` (method POST)

   Then assign the application to your Plivo number.

**How a call works:** Plivo requests the *answer* webhook. The server replies with Plivo XML that speaks the greeting inside `<GetInput inputType="speech">`. Plivo transcribes what the caller says and posts it to the *speech* webhook. The server asks Gemini for a reply and returns more XML that speaks it and listens again. If the caller stays silent, the agent asks again twice, then hangs up politely.

> On trial accounts, Plivo can only call numbers you have verified in the console.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(empty = demo mode)* | Your Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Any Gemini model that supports `generateContent` |
| `AGENT_TIMEZONE` | `Asia/Kolkata` | Time zone for the date and time given to the agent |
| `PLIVO_AUTH_ID` | | Plivo Auth ID |
| `PLIVO_AUTH_TOKEN` | | Plivo Auth Token. Also used to verify webhook signatures |
| `PLIVO_PHONE_NUMBER` | | Your Plivo number, used as caller ID |
| `PUBLIC_BASE_URL` | | Public HTTPS URL of this server, e.g. your ngrok URL |
| `PLIVO_VOICE` | `WOMAN` | Voice for `<Speak>` |
| `PLIVO_LANGUAGE` | `en-US` | Language for speech output and recognition |
| `PLIVO_VALIDATE_SIGNATURE` | `true` | Reject webhooks without a valid Plivo signature |
| `DATABASE_PATH` | `data/agent.db` | SQLite file for settings and transcripts |

## Architecture

```
 Browser UI ──REST──────┐
                        ▼
 Plivo ──webhooks──▶ Controllers ──▶ Services ──▶ Repositories ──▶ SQLite
                                        │
                                        ├──▶ Gemini API (agent replies)
                                        └──▶ Plivo REST API (outbound calls)
```

```
app/
├── main.py                 App factory: routers, error handling, static UI
├── dependencies.py         Wires repositories → services → controllers (FastAPI Depends)
├── schemas.py              Request and response models
├── core/                   Config, database, domain exceptions
├── controllers/            HTTP layer: REST endpoints and Plivo webhooks
├── services/               Business logic: agent, conversations, Gemini, Plivo
├── repositories/           Data access (SQLite)
└── static/                 Web UI (HTML, CSS, JS)
```

The browser and the phone use the same `ConversationService`, so a web chat and a phone call behave identically and both appear in History.

### REST API (`/api/v1`)

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Status, LLM mode (`gemini`/`demo`), model and telephony status |
| GET / PUT | `/agent` | Read or update the agent name, greeting and prompt |
| POST | `/agent/reset` | Restore the default agent |
| POST | `/conversations` | Start a web conversation and return the greeting |
| GET | `/conversations` | List conversations (web and phone) |
| GET / DELETE | `/conversations/{id}` | Get or delete a transcript |
| POST | `/conversations/{id}/messages` | Send a message and get the agent's reply |
| POST | `/conversations/{id}/end` | Mark a conversation as ended |
| GET | `/calls/status` | Whether Plivo is configured |
| POST | `/calls` | Place an outbound phone call: `{"to": "+919876543210"}` |
| POST | `/webhooks/plivo/answer` | Plivo: call answered |
| POST | `/webhooks/plivo/speech` | Plivo: caller speech transcribed |
| POST | `/webhooks/plivo/hangup` | Plivo: call ended |

Errors return JSON `{"detail": "..."}` with status `404`, `422`, `403` (bad webhook signature), `502` (Gemini or Plivo failed) or `503` (feature not configured).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

29 tests cover the REST API, the Gemini request format, and the full Plivo call flow: placing calls, the XML returned at each step, silence handling, hangups and signature checks. External APIs are replaced by a mocked HTTP transport, so no keys are needed.

## Roadmap

- Lower latency with Plivo audio streaming and the Gemini Live API
- Tool calling for real calendar booking (Google Calendar)
- Call recording and post-call summaries
