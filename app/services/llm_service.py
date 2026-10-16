"""Gemini client, with an offline demo mode when no API key is configured."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import Config
from app.core.exceptions import UpstreamError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMService:
    def __init__(self, config: Config, demo_config: dict | None = None):
        self.config = config
        self.demo_config = demo_config or {}

    @property
    def mode(self) -> str:
        return "gemini" if self.config.gemini_api_key else "demo"

    def _clock_context(self) -> str:
        tz_name = self.config.agent_timezone
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            tz_name = "server local time"
            now = datetime.now()
        return f"Current date and time: {now.strftime('%A, %d %B %Y, %I:%M %p')} ({tz_name})."

    async def generate_reply(self, system_prompt: str, history: list[dict]) -> str:
        """history: [{"role": "user"|"assistant", "content": str}, ...], ending with the user turn."""
        if not self.config.gemini_api_key:
            return self.demo_reply(history[-1]["content"] if history else "")

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
            "generationConfig": {
                "temperature": self.config.gemini_temperature,
                "maxOutputTokens": self.config.gemini_max_output_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.config.gemini_timeout_seconds) as client:
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
            except (ValueError, AttributeError):
                detail = resp.text
            raise UpstreamError(f"Gemini error {resp.status_code}: {str(detail)[:300]}")

        try:
            data = resp.json()
            candidates = data.get("candidates") or []
        except (ValueError, AttributeError) as exc:
            raise UpstreamError("Gemini returned an unreadable response.") from exc
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no response")
            raise UpstreamError(f"Gemini returned no answer ({reason}).")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not text:
            reason = candidates[0].get("finishReason", "empty")
            raise UpstreamError(f"Gemini returned an empty answer ({reason}).")
        return text

    def demo_reply(self, user_text: str) -> str:
        """Keyword-matched canned replies from the "demo" section of agent.default.json."""
        words = user_text.lower()
        for rule in self.demo_config.get("replies", []):
            if any(keyword.lower() in words for keyword in rule.get("keywords", [])):
                return rule["reply"]
        return self.demo_config.get("fallback", "Demo mode: add a GEMINI_API_KEY for real answers.")
