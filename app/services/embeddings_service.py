from __future__ import annotations
from app.core.config import settings


import os
from openai import OpenAI

client = OpenAI(api_key=settings.OPENAI_API_KEY)
MODEL = settings.OPENAI_EMBED_MODEL


def embed_query(text: str) -> list[float]:
    resp = client.embeddings.create(model=MODEL, input=[text])
    return resp.data[0].embedding
