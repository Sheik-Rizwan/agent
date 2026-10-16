"""Gemini client, with an offline demo mode when no API key is configured."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import Config
from app.core.exceptions import UpstreamError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMService:
    def __init__(self, config: Config):
        self.config = config

    @property
    def mode(self) -> str:
        return "gemini" if self.config.gemini_api_key else "demo"

    def _clock_context(self) -> str:
        tz_name = self.config.agent_timezone
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError:
            now = datetime.now()
        return f"Current date and time: {now.strftime('%A, %d %B %Y, %I:%M %p')} ({tz_name})."

    async def generate_reply(self, system_prompt: str, history: list[dict]) -> str:
        """history: [{"role": "user"|"assistant", "content": str}, ...], ending with the user turn."""
        if not self.config.gemini_api_key:
            return demo_reply(history[-1]["content"] if history else "")

        contents = [
            {
                "role": "model" if msg["role"] == "assistant" else "user",
                "parts": [{"text": msg["content"]}],
            }
            for msg in history
        ]
        # Gemini requires the conversation to start with a user turn.
        while contents and contents[0]["role"] == "model":
            contents.pop(0)
        if not contents:
            raise UpstreamError("Nothing to reply to.")

        body = {
            "system_instruction": {"parts": [{"text": f"{system_prompt}\n\n{self._clock_context()}"}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1024},
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    GEMINI_URL.format(model=self.config.gemini_model),
                    headers={"x-goog-api-key": self.config.gemini_api_key},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Could not reach Gemini: {exc.__class__.__name__}") from exc

        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                detail = resp.text
            raise UpstreamError(f"Gemini error {resp.status_code}: {detail[:300]}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no response")
            raise UpstreamError(f"Gemini returned no answer ({reason}).")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise UpstreamError("Gemini returned an empty answer.")
        return text


def demo_reply(user_text: str) -> str:
    """Canned replies so the app can be demoed without credentials."""
    text = user_text.lower()
    if any(w in text for w in ("hour", "open", "close", "timing")):
        return "We're open Monday to Friday from 9am to 6pm, and Saturday from 10am to 2pm. We're closed on Sunday."
    if any(w in text for w in ("book", "appointment", "schedule", "slot")):
        return "I'd be happy to book that for you. Could I have your name and a day that works for you?"
    if any(w in text for w in ("price", "cost", "fee", "charge")):
        return "A check-up and cleaning costs 1,500 rupees. Other treatments are priced after an exam."
    if any(w in text for w in ("where", "address", "location")):
        return "We're at 12 MG Road, Bengaluru."
    if any(w in text for w in ("pain", "bleed", "swell", "emergency")):
        return "I'm sorry you're in pain. If it's severe, please come in right away or call emergency services."
    if any(w in text for w in ("thank", "bye")):
        return "You're welcome! Have a great day."
    if any(w in text for w in ("hello", "hi", "hey")):
        return "Hello! How can I help you today?"
    return (
        "I'm running in demo mode right now, so I can only answer about hours, prices, "
        "location, and bookings. Add a Gemini API key to the .env file for full conversations."
    )
