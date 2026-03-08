import os
import uuid
from pathlib import Path
from app.core.config import settings

# Azure is optional for Option 3
try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
except Exception:
    BlobServiceClient = None
    ContentSettings = None

AZURE_AUDIO_CONTAINER = "medi-audio"


def _azure_connection_string() -> str:
    return (settings.AZURE_STORAGE_CONNECTION_STRING or "").strip()


def _azure_container_client():
    if BlobServiceClient is None:
        raise RuntimeError("azure-storage-blob not installed but Azure storage is enabled")

    conn = _azure_connection_string()
    if not conn:
        raise RuntimeError("Missing AZURE_STORAGE_CONNECTION_STRING")

    bsc = BlobServiceClient.from_connection_string(conn)
    container = bsc.get_container_client(AZURE_AUDIO_CONTAINER)
    try:
        container.create_container()
    except Exception:
        pass
    return container


def _use_local() -> bool:
    # Local mode is explicit; otherwise use Azure and fail fast if misconfigured.
    return os.getenv("USE_LOCAL_AUDIO_STORAGE", "").lower() in {"1", "true", "yes"}


def _local_dir() -> Path:
    base = os.getenv("LOCAL_AUDIO_STORAGE_DIR", "/tmp/medi_local_audio")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def upload_audio_bytes(data: bytes, content_type: str, filename: str) -> str:
    """
    Returns a storage path string.
    - Local mode: "local:<absolute_path>"
    - Azure mode: "azure:<blob_path>"
    """
    safe_name = (filename or "voice").replace("/", "_").replace("\\", "_")
    uid = uuid.uuid4().hex

    if _use_local():
        p = _local_dir() / f"{uid}_{safe_name}"
        p.write_bytes(data)
        return f"local:{str(p)}"

    # ---- Azure mode ----
    blob_path = f"voices/{uid}/{safe_name}"
    c = _azure_container_client()
    blob = c.get_blob_client(blob_path)
    blob.upload_blob(
        data,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return f"azure:{blob_path}"


def download_audio_bytes(storage_path: str) -> bytes:
    """
    storage_path is whatever upload_audio_bytes returned.
    """
    if storage_path.startswith("local:"):
        p = Path(storage_path.replace("local:", "", 1))
        return p.read_bytes()

    if storage_path.startswith("azure:"):
        blob_path = storage_path.replace("azure:", "", 1)
        c = _azure_container_client()
        blob = c.get_blob_client(blob_path)
        return blob.download_blob().readall()

    # Backward compatibility: if you stored raw blob_path before
    # treat it as local file path if it exists
    p = Path(storage_path)
    if p.exists():
        return p.read_bytes()

    raise ValueError(f"Unknown storage path format: {storage_path}")


def delete_audio(storage_path: str) -> None:
    """
    Optional cleanup. In local mode we can delete after processing.
    """
    try:
        if storage_path.startswith("local:"):
            p = Path(storage_path.replace("local:", "", 1))
            if p.exists():
                p.unlink()
            return

        if storage_path.startswith("azure:"):
            blob_path = storage_path.replace("azure:", "", 1)
            c = _azure_container_client()
            blob = c.get_blob_client(blob_path)
            blob.delete_blob(delete_snapshots="include")
            return
    except Exception:
        pass
