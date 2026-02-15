from twilio.rest import Client
from app.core.config import settings

def send_whatsapp_menu(to_number: str) -> None:
    """
    Sends the WhatsApp Quick Reply template (poll-style buttons).
    Works with WhatsApp Sandbox or direct WhatsApp sender.
    Requires: MENU_TEMPLATE_SID (HX...)
    """
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    client.messages.create(
        from_=settings.TWILIO_WHATSAPP_NUMBER,  # 👈 sandbox or prod number
        to=to_number,
        content_sid=settings.MENU_TEMPLATE_SID,
    )
