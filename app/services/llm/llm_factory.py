import os
from llm.anthropic_client import AnthropicClient

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    return AnthropicClient()

