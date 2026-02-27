from twilio.rest import Client
from app.core.config import settings

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
