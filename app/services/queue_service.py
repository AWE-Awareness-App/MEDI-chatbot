from redis import Redis
from rq import Queue
from app.core.config import settings
from app.jobs.tts_jobs import whatsapp_tts_reply_job

# Add these to env/settings:
# settings.REDIS_URL = "redis://..."
redis_conn = Redis.from_url(settings.REDIS_URL)
q = Queue("medi-voice", connection=redis_conn, default_timeout=900)  # 15 min

def enqueue_voice_job(payload: dict) -> str:
    print("heere 3 .1")
    job = q.enqueue("app.services.voice_worker.process_voice_job", payload)
    return job.id



def enqueue_whatsapp_audio_reply(*, to_e164: str, text: str) -> None:
    """
    Enqueue audio generation + Twilio send.
    This runs in the worker container.
    """
    print("heere 3 .2")

    conn = Redis.from_url(settings.REDIS_URL)
    q = Queue("medi-reply", connection=conn)   # change name if your worker uses a different queue
    q.enqueue(whatsapp_tts_reply_job, to_e164, text)