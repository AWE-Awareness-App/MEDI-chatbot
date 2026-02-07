from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from db.session import get_db
from services.chat_service import handle_incoming_message

router = APIRouter()

@router.post("/webhook/twilio")
def twilio_webhook(
    From: str = Form(...),   # whatsapp:+1647xxxxxxx
    Body: str = Form(...),
    db: Session = Depends(get_db),
):
    result = handle_incoming_message(
        db=db,
        source="whatsapp",
        external_id=From,
        text=Body,
    )

    twiml = MessagingResponse()
    twiml.message(result["reply"])

    # ✅ IMPORTANT: return XML, not JSON
    return Response(
        content=str(twiml),
        media_type="application/xml"
    )
