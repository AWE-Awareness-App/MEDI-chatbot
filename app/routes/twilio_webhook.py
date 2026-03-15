import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from app.core.observability import trace_call
from app.db.session import get_db
from app.services.chat_service import handle_incoming_message
from app.services.language_service import resolve_language
from app.services.twilio_sender import send_whatsapp_menu
from app.services.voice_jobs import create_voice_job
from app.services.voice_worker import process_voice_job

from app.services.twilio_sender import send_whatsapp_typing_indicator


router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/whatsapp/webhook")
@router.post("/api/whatsapp/webhook/")
@router.post("/webhook/twilio")
@router.post("/webhook/twilio/")
@trace_call
def twilio_webhook(
    background_tasks: BackgroundTasks,
    From: str = Form(...),   # e.g. "whatsapp:+1647xxxxxxx"
    Body: str | None = Form(None),
    NumMedia: int = Form(0),
    MediaUrl0: str | None = Form(None),
    MediaContentType0: str | None = Form(None),
    db: Session = Depends(get_db),
    MessageSid: str | None = Form(None)

):
    text = (Body or "").strip()
    logger.info(
        "twilio webhook received: has_text=%s num_media=%s media_type=%s",
        bool(text),
        NumMedia,
        MediaContentType0,
    )

    # 1) Menu fast path (template buttons)
    if text.lower() in {"menu", "help", "start"}:
        logger.info("twilio webhook fast-path: sending menu template")
        try:
            send_whatsapp_menu(to_number=From)
            twiml = MessagingResponse()
            return Response(content=str(twiml), media_type="application/xml")
        except Exception as exc:
            logger.exception("twilio menu template failed; falling back to text reply: %s", exc)
            result = handle_incoming_message(
                db=db,
                source="whatsapp",
                external_id=From,
                text=text,
            )
            twiml = MessagingResponse()
            twiml.message(result["reply"])
            return Response(content=str(twiml), media_type="application/xml")

    # 2) Voice note path (WhatsApp audio)
    if NumMedia and MediaUrl0 and (MediaContentType0 or "").startswith("audio/"):
        preferred_language = None
        if text:
            preferred_language = resolve_language(text, default="en")

        job_id = create_voice_job(
            db=db,
            source="whatsapp",
            user_id=From,
            twilio_media_url=MediaUrl0,
            audio_blob_path=None,
            twilio_message_sid=MessageSid,
        )

        background_tasks.add_task(
            process_voice_job,
            {"job_id": job_id, "preferred_language": preferred_language},
        )
        logger.info(
            "twilio webhook voice-path: queued voice job job_id=%s preferred_language=%s",
            job_id,
            preferred_language,
        )
        if MessageSid:
            try:
                send_whatsapp_typing_indicator(MessageSid)
            except Exception:
                logger.exception("twilio typing indicator failed")

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
    logger.info("twilio webhook text-path completed")

    # Keep TwiML reply for text messages (your current behavior)
    twiml = MessagingResponse()
    twiml.message(result["reply"])
    return Response(content=str(twiml), media_type="application/xml")

