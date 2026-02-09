from pydantic import BaseModel, Field
from datetime import datetime

class ChatRequest(BaseModel):
    user_id: str = Field(..., description="External user id e.g. whatsapp:+1647...")
    text: str

class ChatResponse(BaseModel):
    conversation_id: str
    reply: str


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

class ChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[MessageOut]