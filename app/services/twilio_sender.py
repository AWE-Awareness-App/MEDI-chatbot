import logging

import requests
from twilio.rest import Client

from app.core.observability import instrument_module_functions
from app.core.config import settings


_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
logger = logging.getLogger(__name__)


def _normalize_whatsapp_to(to_number: str) -> str:
    value = (to_number or "").strip().replace(" ", "")
    if not value:
        raise ValueError("Missing destination number")
    if value.startswith("whatsapp:"):
        suffix = value.split(":", 1)[1]
        if suffix.startswith("+"):
            return value
        if suffix.isdigit():
            return f"whatsapp:+{suffix}"
        return value
    if value.startswith("+"):
        return f"whatsapp:{value}"
    return f"whatsapp:+{value}"


def send_whatsapp_menu(to_number: str) -> None:
    """
    Sends the WhatsApp Quick Reply template (poll-style buttons).
    Requires: MENU_TEMPLATE_SID (HX...)
    """
    to_value = _normalize_whatsapp_to(to_number)
    _client.messages.create(
        from_=settings.TWILIO_WHATSAPP_NUMBER,
        to=to_value,
        content_sid=settings.MENU_TEMPLATE_SID,
    )
    logger.info("twilio menu sent to=%s", to_value)


def send_whatsapp_text(to_number: str, body: str) -> None:
    """
    Send a normal WhatsApp text message via Twilio REST.
    """
    to_value = _normalize_whatsapp_to(to_number)
    _client.messages.create(
        from_=settings.TWILIO_WHATSAPP_NUMBER,
        to=to_value,
        body=body,
    )
    logger.info("twilio text sent to=%s chars=%s", to_value, len(body or ""))


def send_whatsapp_audio(to_e164: str, ogg_url: str) -> str:
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    from_whatsapp = settings.TWILIO_WHATSAPP_NUMBER  # e.g. "whatsapp:+14155238886"
    to_value = _normalize_whatsapp_to(to_e164)

    client = Client(account_sid, auth_token)
    msg = client.messages.create(
        from_=from_whatsapp,
        # to=f"whatsapp:{to_e164}",
        to=to_value,

        media_url=[ogg_url],
    )
    logger.info("twilio audio sent to=%s", to_value)
    return msg.sid


def send_whatsapp_typing_indicator(message_sid: str) -> None:
    """
    Triggers WhatsApp typing indicator for up to ~25s or until you send a message.
    Twilio WhatsApp Typing Indicators API (public beta).
    """
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN

    url = "https://messaging.twilio.com/v2/Indicators/Typing.json"
    data = {
        "messageId": message_sid,
        "channel": "whatsapp",
    }

    r = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=10)
    r.raise_for_status()
    logger.info("twilio typing indicator sent sid=%s", message_sid)


instrument_module_functions(globals(), include_private=False)
