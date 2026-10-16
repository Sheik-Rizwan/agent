# Workflows

Step-by-step flows through the code, with the file and function for each step. Interactive versions of each diagram are in [`docs/diagrams/`](diagrams/).

1. [Web voice conversation](#1-web-voice-conversation)
2. [Phone call with Plivo](#2-phone-call-with-plivo)
3. [Login and every authenticated request](#3-login-and-every-authenticated-request)
4. [Conversation lifecycle](#4-conversation-lifecycle)
5. [User management](#5-user-management)
6. [Changing the agent](#6-changing-the-agent)

---

## 1. Web voice conversation

> Interactive: [`diagrams/02-web-voice-conversation.html`](diagrams/02-web-voice-conversation.html)

```mermaid
sequenceDiagram
    actor U as User
    participant B as Browser (app.js)
    participant API as conversation_controller
    participant CS as ConversationService
    participant R as ConversationRepository
    participant L as LLMService
    participant G as Gemini

    U->>B: Start call
    B->>API: POST /api/v1/conversations
    API->>CS: start("web", created_by)
    CS->>R: create() + add greeting
    API-->>B: {id, greeting}
    B->>U: speaks greeting (speechSynthesis)
    U->>B: talks (SpeechRecognition → text)
    B->>API: POST /conversations/{id}/messages {text}
    API->>CS: reply(id, text)
    CS->>R: add_message(user)
    CS->>L: generate_reply(system_prompt, last N messages)
    L->>G: generateContent
    G-->>L: reply text
    CS->>R: add_message(assistant)
    API-->>B: {reply}
    B->>U: speaks reply, then reopens mic (hands-free)
```

| Step | Code |
|---|---|
| Mic → text, text → speech, hands-free loop, interrupting | `app/static/app.js`: `startListening`, `speak`, `afterAgentTurn` |
| Stale callbacks ignored when you interrupt or end the call | `app.js`: `state.turn` counter |
| Start, reply, end | `ConversationService.start / reply / end` |
| History trimmed to `LLM_MAX_HISTORY_MESSAGES` | `ConversationService.reply` |
| Date and time added to the prompt | `LLMService._clock_context` |
| No API key → keyword replies from `config/agent.default.json` | `LLMService.demo_reply` |
| Gemini fails → HTTP 502 with the reason | `LLMService.generate_reply` → `UpstreamError` |

---

## 2. Phone call with Plivo

> Interactive: [`diagrams/03-phone-call-plivo.html`](diagrams/03-phone-call-plivo.html)

```mermaid
sequenceDiagram
    participant UI as Browser / API client
    participant API as Agent server
    participant P as Plivo
    actor C as Caller
    participant G as Gemini

    UI->>API: POST /api/v1/calls {to}
    API->>P: POST /v1/Account/{id}/Call/ (answer_url, hangup_url)
    P->>C: rings
    C-->>P: answers
    P->>API: POST /webhooks/plivo/answer (signed)
    API-->>P: XML: GetInput(speech) + Speak(greeting)
    P->>C: speaks greeting, listens
    loop each turn
        C->>P: speaks
        P->>API: POST /webhooks/plivo/speech?conversation_id&silent (Speech=text)
        API->>G: generateContent
        G-->>API: reply
        API-->>P: XML: GetInput + Speak(reply)
        P->>C: speaks reply, listens
    end
    alt caller silent
        P->>API: Redirect → /speech with no Speech
        API-->>P: Speak(phone_not_heard) … after PLIVO_MAX_SILENT_TURNS: Speak(goodbye) + Hangup
    end
    P->>API: POST /webhooks/plivo/hangup (CallUUID)
    API->>API: conversation ended_at set
```

| Step | Code |
|---|---|
| Place the call (REST) | `call_controller.place_call` → `PlivoService.place_call` |
| Phone number check (8–15 digits) | `plivo_service.normalize_number` |
| Every webhook's signature verified | `plivo_webhook_controller.verify_plivo_signature` |
| Answer: create the phone conversation (retries reuse it) | `plivo_webhook_controller.answer` |
| Build Plivo XML (escaped safely) | `PlivoService.listen_xml / hangup_xml` |
| Silence counting via the `silent` query parameter | `plivo_webhook_controller.speech` |
| Gemini fails mid-call → speak `phone_technical_issue`, keep listening | `plivo_webhook_controller.speech` |
| Inbound calls | Point your Plivo number's answer URL at `/api/v1/webhooks/plivo/answer` |

---

## 3. Login and every authenticated request

> Interactive: [`diagrams/04-auth-and-access.html`](diagrams/04-auth-and-access.html)

```mermaid
flowchart TD
    subgraph Login["POST /api/v1/auth/login"]
        L1[username + password] --> L2{locked out?<br/>≥ LOGIN_MAX_ATTEMPTS failures}
        L2 -- yes --> L429[429 + Retry-After]
        L2 -- no --> L3{scrypt verify<br/>dummy hash if user unknown}
        L3 -- wrong --> L401[401 · failure recorded]
        L3 -- ok --> L4[new random token<br/>store SHA-256 in sessions.json]
        L4 --> L5[Set-Cookie: HttpOnly, SameSite=Strict<br/>+ token in body for API clients]
    end

    subgraph Request["Any protected request"]
        R1[request] --> R2{write from another Origin?}
        R2 -- yes --> R403a[403]
        R2 -- no --> R3[token from Bearer header or cookie]
        R3 --> R4{session found and not expired?<br/>user still exists?}
        R4 -- no --> R401[401 → UI shows login]
        R4 -- yes --> R5{admin-only route?}
        R5 -- "yes, role=user" --> R403b[403]
        R5 -- "no / admin" --> R6[controller → service → repository]
    end
```

| Step | Code |
|---|---|
| Origin check, security headers | `app/main.py`: `security_middleware` |
| Rate limiting | `auth_service.LoginRateLimiter` |
| Password check | `core/security.verify_password` |
| Session create, validate, expire | `AuthService.login / authenticate`, `SessionRepository` |
| Guards | `dependencies.get_current_user`, `dependencies.require_admin` |
| Logout (server-side session deleted) | `auth_controller.logout` |
| UI: any 401 → login screen | `app.js`: `api()` → `showLogin` |

---

## 4. Conversation lifecycle

> Interactive: [`diagrams/05-conversation-lifecycle.html`](diagrams/05-conversation-lifecycle.html)

```mermaid
stateDiagram-v2
    [*] --> Active: start (web) or answer webhook (phone), greeting stored
    Active --> Active: user message → agent reply
    Active --> Waiting: phone - caller silent
    Waiting --> Active: caller speaks
    Waiting --> Waiting: re-prompt (phone_not_heard)
    Waiting --> Ended: silence limit reached → goodbye + hangup
    Active --> Ended: End call (web) / hangup webhook (phone)
    Ended --> Ended: new messages rejected (409)
    Ended --> Deleted: admin deletes
    Active --> Deleted: admin deletes
    Deleted --> [*]
```

---

## 5. User management

```mermaid
flowchart LR
    A[First start] --> B{users.json empty?}
    B -- "yes + ADMIN_USERNAME/PASSWORD set" --> C[create admin]
    B -- "yes, not set" --> D[log: run python -m app.cli create-user NAME --role admin]
    B -- no --> E[nothing]
    C & D & E --> F[Admin → Users tab]
    F --> G[create user/admin]
    F --> H[reset password → that user logged out everywhere]
    F --> I[delete user → logged out everywhere<br/>not yourself, not the last admin]
    J[Any user → Account tab] --> K[change own password<br/>needs current password; other sessions logged out]
```

The same actions exist on the command line (works while the server is stopped):

```bash
python -m app.cli create-user alice --role admin
python -m app.cli list-users
python -m app.cli reset-password alice
python -m app.cli delete-user alice
```

---

## 6. Changing the agent

```mermaid
flowchart LR
    D[config/agent.default.json<br/>defaults, in git] --> M[AgentService.get<br/>defaults ⊕ overrides]
    O[data/agent.json<br/>admin's changes] --> M
    M --> UI[Agent tab form<br/>fields generated from keys]
    M --> W[web greeting + system prompt]
    M --> P[phone phrases:<br/>phone_not_heard, phone_goodbye_silent,<br/>phone_technical_issue, phone_unknown_call]
    UI -- "PUT /api/v1/agent (admin)" --> O
    UI -- "POST /api/v1/agent/reset (admin)" --> X[delete agent.json]
```

- **Change the default persona for everyone:** edit `config/agent.default.json`.
- **Change it for this deployment only:** use the Agent tab. That writes `data/agent.json`.
- **Add a new field:** add a key under `agent` in the defaults file. It shows up in the API and the UI with no code change. Required keys: `agent_name`, `greeting`, `system_prompt`, and the four `phone_*` phrases.
- **Change demo-mode answers:** edit the `demo.replies` keyword list in the same file.
