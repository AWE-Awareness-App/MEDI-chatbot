from redis import Redis
from rq import Worker, Queue

from app.core.config import settings

QUEUE_NAME = "medi-voice"

def main():
    redis_conn = Redis.from_url(settings.REDIS_URL)

    # RQ 2.x: pass connection directly
    q = Queue(name=QUEUE_NAME, connection=redis_conn)
    worker = Worker([q], connection=redis_conn)

    # with_scheduler=False keeps it simple and avoids scheduler multiprocessing issues
    worker.work(with_scheduler=False)

if __name__ == "__main__":
    main()