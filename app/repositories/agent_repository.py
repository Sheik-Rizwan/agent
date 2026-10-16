"""Data access for the agent configuration.

- The shipped defaults live in config/agent.default.json (read-only, in git).
- Overrides saved from the UI live in <DATA_DIR>/agent.json (local, ignored by git).
"""

import json
import logging
from pathlib import Path

from app.core.exceptions import NotConfiguredError
from app.core.json_store import JsonFile

logger = logging.getLogger(__name__)

# Keys the code relies on. Any extra keys in the defaults file are allowed and
# simply show up as additional editable fields.
REQUIRED_KEYS = (
    "agent_name",
    "greeting",
    "system_prompt",
    "phone_not_heard",
    "phone_goodbye_silent",
    "phone_technical_issue",
    "phone_unknown_call",
)


class AgentRepository:
    def __init__(self, data_dir: Path, defaults_path: Path):
        self.overrides = JsonFile(data_dir / "agent.json", dict)
        self.defaults_path = defaults_path

    def load_defaults(self) -> dict:
        """Returns the whole defaults document: {"agent": {...}, "demo": {...}}."""
        try:
            with open(self.defaults_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as exc:
            raise NotConfiguredError(f"Agent defaults file not found: {self.defaults_path}") from exc
        except json.JSONDecodeError as exc:
            raise NotConfiguredError(f"Agent defaults file is not valid JSON: {exc}") from exc
        agent = data.get("agent") if isinstance(data, dict) else None
        if not isinstance(agent, dict):
            raise NotConfiguredError("Agent defaults file must contain an \"agent\" object")
        missing = [k for k in REQUIRED_KEYS if not isinstance(agent.get(k), str) or not agent[k].strip()]
        if missing:
            raise NotConfiguredError(f"Agent defaults file is missing text for: {', '.join(missing)}")
        return data

    def get_overrides(self) -> dict:
        data = self.overrides.read()
        return data if isinstance(data, dict) else {}

    def save_overrides(self, values: dict) -> None:
        with self.overrides.update() as data:
            data.update(values)

    def clear_overrides(self) -> None:
        self.overrides.delete()
