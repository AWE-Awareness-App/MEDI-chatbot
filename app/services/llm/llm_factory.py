from llm.anthropic_client import AnthropicClient
from core.config import settings

def get_llm():
    provider = (settings.LLM_PROVIDER or "openai").lower()
    return AnthropicClient()

