from sqlalchemy import text
from sqlalchemy.engine import Engine


def ensure_runtime_schema(engine: Engine) -> None:
    """
    Apply additive schema patches required by newer code paths.
    Safe to run repeatedly.
    """
    statements = [
        "ALTER TABLE voice_jobs ADD COLUMN IF NOT EXISTS reply_audio_url TEXT",
        "ALTER TABLE voice_jobs ADD COLUMN IF NOT EXISTS reply_audio_mime VARCHAR",
        "ALTER TABLE voice_jobs ADD COLUMN IF NOT EXISTS reply_audio_path VARCHAR",
    ]

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
