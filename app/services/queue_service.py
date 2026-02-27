from redis import Redis
from rq import Queue
from app.core.config import settings

# Add these to env/settings:
# settings.REDIS_URL = "redis://..."
redis_conn = Redis.from_url(settings.REDIS_URL)
q = Queue("medi-voice", connection=redis_conn, default_timeout=900)  # 15 min

def enqueue_voice_job(payload: dict) -> str:
    job = q.enqueue("app.services.voice_worker.process_voice_job", payload)
    return job.id
