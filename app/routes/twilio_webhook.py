from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from app.db.session import get_db
from app.services.chat_service import handle_incoming_message
from app.services.twilio_sender import send_whatsapp_menu

router = APIRouter()

@router.post("/webhook/twilio")
def twilio_webhook(
    From: str = Form(...),   # e.g. "whatsapp:+1647xxxxxxx"
    Body: str = Form(...),
    db: Session = Depends(get_db),
):
    text = (Body or "").strip()
    print("numer",Form(...))

    # 1) If user wants menu, send the quick-reply template (buttons)
    if text.lower() in {"menu", "help", "start"}:
        send_whatsapp_menu(to_number=From)

        # Return empty TwiML so Twilio doesn't also send a second text reply
        twiml = MessagingResponse()
        return Response(content=str(twiml), media_type="application/xml")

    # 2) Otherwise normal chat pipeline (stores msg + replies)
    result = handle_incoming_message(
        db=db,
        source="whatsapp",
        external_id=From,
        text=text,
    )

    twiml = MessagingResponse()
    twiml.message(result["reply"])
    return Response(content=str(twiml), media_type="application/xml")
