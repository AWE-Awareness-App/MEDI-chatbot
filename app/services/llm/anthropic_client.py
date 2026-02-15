from anthropic import AsyncAnthropic
from app.core.config import settings
from llm.base import LLMClient

MODEL = settings.ANTHROPIC_MODEL or "claude-3-5-sonnet-latest"

SYSTEM_PROMPT = """You are MEDI, a calm meditation and mental-wellness assistant.
- Supportive, non-medical guidance only.
- Prefer short, actionable exercises.
- Offer Breathing / Sleep / Stress options when helpful.
- If self-harm intent or imminent danger: encourage contacting local emergency services.
Tone: calm, brief, kind.
"""

class AnthropicClient(LLMClient):
    def __init__(self):
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = AsyncAnthropic(api_key=api_key)

    async def generate(self, messages: list[dict]) -> str:
        """
        messages = [{"role":"user"|"assistant","content":"..."}]
        Claude uses system separately (not as role in messages)
        """

        resp = await self.client.messages.create(
            model=MODEL,
            max_tokens=450,
            temperature=0.4,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        return "".join(
            block.text for block in resp.content
            if block.type == "text"
        ).strip()
