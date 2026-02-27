import os
import uuid
from pathlib import Path
from typing import Optional

# Azure is optional for Option 3
try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
except Exception:
    BlobServiceClient = None
    ContentSettings = None


def _use_local() -> bool:
    # Preferred toggle
    if os.getenv("USE_LOCAL_AUDIO_STORAGE", "").lower() in {"1", "true", "yes"}:
        return True
    # Auto fallback if no azure conn string
    if not os.getenv("AZURE_STORAGE_CONNECTION_STRING"):
        return True
    return False


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

    # ---- Azure mode (kept for easy switch later) ----
    if BlobServiceClient is None:
        raise RuntimeError("azure-storage-blob not installed but Azure storage is enabled")

    conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    container = os.environ["AZURE_BLOB_CONTAINER"]
    blob_path = f"voices/{uid}/{safe_name}"

    bsc = BlobServiceClient.from_connection_string(conn)
    c = bsc.get_container_client(container)
    try:
        c.create_container()
    except Exception:
        pass

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
        if BlobServiceClient is None:
            raise RuntimeError("azure-storage-blob not installed but Azure storage is enabled")

        conn = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        container = os.environ["AZURE_BLOB_CONTAINER"]
        blob_path = storage_path.replace("azure:", "", 1)

        bsc = BlobServiceClient.from_connection_string(conn)
        c = bsc.get_container_client(container)
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
    except Exception:
        pass