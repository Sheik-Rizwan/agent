"""Plivo telephony: REST API for outbound calls, Plivo XML for call control,
and webhook signature checks."""

import base64
import hashlib
import hmac
import re
import xml.etree.ElementTree as ET

import httpx

from app.core.config import Config
from app.core.exceptions import NotConfiguredError, UpstreamError, ValidationError

PLIVO_API = "https://api.plivo.com/v1/Account/{auth_id}/Call/"
E164 = re.compile(r"^\+?[1-9]\d{7,14}$")  # 8-15 digits, no leading 0


def normalize_number(number: str) -> str:
    """Accept '+91 98765-43210' style input; return digits only, as Plivo expects."""
    cleaned = re.sub(r"[\s\-()]", "", number or "")
    if not E164.match(cleaned):
        raise ValidationError("Phone number must be in international format, e.g. +919876543210")
    return cleaned.lstrip("+")


class PlivoService:
    def __init__(self, config: Config):
        self.config = config

    # ---------- REST API ----------

    async def place_call(self, to_number: str, answer_url: str, hangup_url: str) -> dict:
        if not self.config.plivo_configured:
            raise NotConfiguredError(
                "Plivo is not configured. Set PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN and PLIVO_PHONE_NUMBER in .env."
            )
        body = {
            "from": normalize_number(self.config.plivo_phone_number),
            "to": normalize_number(to_number),
            "answer_url": answer_url,
            "answer_method": "POST",
            "hangup_url": hangup_url,
            "hangup_method": "POST",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    PLIVO_API.format(auth_id=self.config.plivo_auth_id),
                    auth=(self.config.plivo_auth_id, self.config.plivo_auth_token),
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Could not reach Plivo: {exc.__class__.__name__}") from exc

        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code not in (200, 201):
            detail = data.get("error") or resp.text or "unknown error"
            raise UpstreamError(f"Plivo error {resp.status_code}: {str(detail)[:300]}")
        return {"request_uuid": data.get("request_uuid"), "message": data.get("message", "call fired")}

    # ---------- webhook security ----------

    def is_valid_signature(self, url: str, nonce: str, signature: str) -> bool:
        """Plivo V2 signature: base64(HMAC-SHA256(auth_token, url_without_query + nonce))."""
        if not (self.config.plivo_auth_token and nonce and signature):
            return False
        base_url = url.split("?", 1)[0]
        digest = hmac.new(
            self.config.plivo_auth_token.encode(), (base_url + nonce).encode(), hashlib.sha256
        ).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature.strip())

    # ---------- Plivo XML ----------

    def _speak(self, parent: ET.Element, text: str) -> None:
        speak = ET.SubElement(
            parent, "Speak", voice=self.config.plivo_voice, language=self.config.plivo_language
        )
        speak.text = text

    def listen_xml(self, prompt: str, action_url: str) -> str:
        """Say `prompt`, then capture the caller's speech and POST it to `action_url`.
        The caller can talk over the prompt. If they stay silent, Plivo falls through
        to the Redirect, which hits the same URL with no speech."""
        response = ET.Element("Response")
        get_input = ET.SubElement(
            response,
            "GetInput",
            action=action_url,
            method="POST",
            inputType="speech",
            language=self.config.plivo_language,
        )
        self._speak(get_input, prompt)
        redirect = ET.SubElement(response, "Redirect", method="POST")
        redirect.text = action_url
        return _to_xml(response)

    def hangup_xml(self, text: str) -> str:
        response = ET.Element("Response")
        self._speak(response, text)
        ET.SubElement(response, "Hangup")
        return _to_xml(response)


def _to_xml(element: ET.Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(element, encoding="unicode")
