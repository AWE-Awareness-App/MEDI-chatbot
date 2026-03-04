from twilio.rest import Client
from app.core.config import settings
import requests


_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

def send_whatsapp_menu(to_number: str) -> None:
    """
    Sends the WhatsApp Quick Reply template (poll-style buttons).
    Requires: MENU_TEMPLATE_SID (HX...)
    """
    _client.messages.create(
        from_=settings.TWILIO_WHATSAPP_NUMBER,
        to=to_number,
        content_sid=settings.MENU_TEMPLATE_SID,
    )

def send_whatsapp_text(to_number: str, body: str) -> None:
    """
    Send a normal WhatsApp text message via Twilio REST.
    """
    _client.messages.create(
        from_=settings.TWILIO_WHATSAPP_NUMBER,
        to=to_number,
        body=body,
    )
def send_whatsapp_audio(to_e164: str, ogg_url: str) -> str:
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    from_whatsapp = settings.TWILIO_WHATSAPP_NUMBER  # e.g. "whatsapp:+14155238886"
    to_value = to_e164 if to_e164.startswith("whatsapp:") else f"whatsapp:{to_e164}"

    client = Client(account_sid, auth_token)
    msg = client.messages.create(
        from_=from_whatsapp,
        # to=f"whatsapp:{to_e164}",
        to=to_value,

        media_url=[ogg_url],
    )
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