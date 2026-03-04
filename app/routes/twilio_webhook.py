from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from app.db.session import get_db
from app.services.chat_service import handle_incoming_message
from app.services.twilio_sender import send_whatsapp_menu, send_whatsapp_text
from app.services.voice_jobs import create_voice_job
from app.services.queue_service import enqueue_voice_job

from app.services.twilio_sender import send_whatsapp_typing_indicator


router = APIRouter()

@router.post("/webhook/twilio")
def twilio_webhook(
    From: str = Form(...),   # e.g. "whatsapp:+1647xxxxxxx"
    Body: str | None = Form(None),
    NumMedia: int = Form(0),
    MediaUrl0: str | None = Form(None),
    MediaContentType0: str | None = Form(None),
    db: Session = Depends(get_db),
    MessageSid: str | None = Form(None)

):
    text = (Body or "").strip()

    # 1) Menu fast path (template buttons)
    if text.lower() in {"menu", "help", "start"}:
        send_whatsapp_menu(to_number=From)
        twiml = MessagingResponse()
        return Response(content=str(twiml), media_type="application/xml")

    # 2) Voice note path (WhatsApp audio)
    if NumMedia and MediaUrl0 and (MediaContentType0 or "").startswith("audio/"):
        job_id = create_voice_job(
            db=db,
            source="whatsapp",
            user_id=From,
            twilio_media_url=MediaUrl0,
            audio_blob_path=None,
            twilio_message_sid=MessageSid,   # ✅ store it
        )

        enqueue_voice_job({"job_id": job_id})
        if MessageSid:
            send_whatsapp_typing_indicator(MessageSid)

        # Immediate ack via REST (don’t block webhook)
        # send_whatsapp_text(to_number=From, body="Got your voice note — transcribing now…")

        twiml = MessagingResponse()
        return Response(content=str(twiml), media_type="application/xml")

    # 3) Normal text pipeline (existing behavior)
    if not text:
        # Don't send TwiML reply for empty body; just ignore
        twiml = MessagingResponse()
        return Response(content=str(twiml), media_type="application/xml")

    result = handle_incoming_message(
        db=db,
        source="whatsapp",
        external_id=From,
        text=text,
    )

    # Keep TwiML reply for text messages (your current behavior)
    twiml = MessagingResponse()
    twiml.message(result["reply"])
    return Response(content=str(twiml), media_type="application/xml")
