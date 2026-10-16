"""Business logic for the agent's configuration.

The effective configuration = defaults from config/agent.default.json,
overlaid with whatever an admin saved from the UI. Only keys that exist in the
defaults file are accepted, so the defaults file defines the schema.
"""

from app.core.exceptions import ValidationError
from app.repositories.agent_repository import AgentRepository

MAX_FIELD_LENGTH = 20000


class AgentService:
    def __init__(self, repo: AgentRepository):
        self.repo = repo

    def defaults(self) -> dict[str, str]:
        return dict(self.repo.load_defaults()["agent"])

    def demo_config(self) -> dict:
        return self.repo.load_defaults().get("demo", {})

    def get(self) -> dict[str, str]:
        defaults = self.defaults()
        overrides = self.repo.get_overrides()
        return {key: overrides.get(key, default) for key, default in defaults.items()}

    def update(self, values: dict[str, str]) -> dict[str, str]:
        """Update any subset of the fields. Unknown keys and blank values are rejected."""
        if not values:
            raise ValidationError("Nothing to update")
        unknown = sorted(set(values) - set(self.defaults()))
        if unknown:
            raise ValidationError(f"Unknown field(s): {', '.join(unknown)}")
        for key, value in values.items():
            if not value.strip():
                raise ValidationError(f"'{key}' can't be empty")
            if len(value) > MAX_FIELD_LENGTH:
                raise ValidationError(f"'{key}' is longer than {MAX_FIELD_LENGTH} characters")
        self.repo.save_overrides(values)
        return self.get()

    def reset(self) -> dict[str, str]:
        self.repo.clear_overrides()
        return self.get()
