"""Business logic for the agent's configuration (name, greeting, prompt)."""

from app.repositories.settings_repository import SettingsRepository

DEFAULT_AGENT = {
    "agent_name": "Ava",
    "greeting": "Hi, this is Ava from Sunrise Dental. How can I help you today?",
    "system_prompt": (
        "You are Ava, a warm and efficient voice receptionist for Sunrise Dental, "
        "a family dental clinic.\n\n"
        "Clinic facts:\n"
        "- Hours: Monday to Friday 9am to 6pm, Saturday 10am to 2pm, closed Sunday.\n"
        "- Services: check-ups and cleaning, fillings, root canals, whitening, braces consultations.\n"
        "- A check-up and cleaning costs 1,500 rupees. Other prices are given after an exam.\n"
        "- Address: 12 MG Road, Bengaluru.\n\n"
        "How to talk:\n"
        "- You are speaking out loud, so keep replies to one to three short sentences.\n"
        "- Never use markdown, bullet points, emojis, or lists.\n"
        "- Ask one question at a time.\n"
        "- To book an appointment, collect the caller's name, preferred day and time, "
        "and reason for the visit, then read the details back to confirm.\n"
        "- If you don't know something, say so and offer to have the clinic call back.\n"
        "- Never give medical diagnoses. For severe pain, bleeding, or swelling, "
        "advise the caller to visit urgently or call emergency services."
    ),
}


class AgentService:
    def __init__(self, repo: SettingsRepository):
        self.repo = repo

    def get(self) -> dict[str, str]:
        stored = self.repo.get_all()
        return {key: stored.get(key, default) for key, default in DEFAULT_AGENT.items()}

    def update(self, values: dict[str, str]) -> dict[str, str]:
        self.repo.upsert_many({k: v for k, v in values.items() if k in DEFAULT_AGENT})
        return self.get()

    def reset(self) -> dict[str, str]:
        self.repo.clear()
        return self.get()
