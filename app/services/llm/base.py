from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Dict

ChatMsg = Dict[str, str]  # {"role": "...", "content": "..."}

class LLMClient(ABC):
    @abstractmethod
    async def generate(self, messages: List[ChatMsg]) -> str:
        """
        messages example:
          [{"role":"user","content":"hi"}, {"role":"assistant","content":"hello"}]
        NOTE: System prompt is injected inside provider client.
        """
        raise NotImplementedError
