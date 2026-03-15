from pydantic import BaseModel, Field
from datetime import datetime

class ChatRequest(BaseModel):
    user_id: str = Field(..., description="External user id e.g. whatsapp:+1647...")
    text: str
    language: str = Field(
        default="en",
        description="Reply language. Defaults to en. Supported: en, fr, ja, ar.",
    )

class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    language: str | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

class ChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[MessageOut]
