import os
import uuid
from datetime import datetime, timedelta, timezone

# Azure is required for voice storage
try:
    from azure.storage.blob import (
        BlobSasPermissions,
        BlobServiceClient,
        ContentSettings,
        generate_blob_sas,
    )
except Exception:
    BlobSasPermissions = None
    BlobServiceClient = None
    ContentSettings = None
    generate_blob_sas = None


def _require_azure_sdk() -> None:
    if BlobServiceClient is None or ContentSettings is None:
        raise RuntimeError("azure-storage-blob not installed")


def _connection_string() -> str:
    conn = (os.getenv("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
    if not conn:
        raise RuntimeError("Missing AZURE_STORAGE_CONNECTION_STRING")
    return conn


def _container_name() -> str:
    # Backward compatible: prefer new key, fall back to old one.
    return (
        (os.getenv("AZURE_STORAGE_CONTAINER") or "").strip()
        or (os.getenv("AZURE_BLOB_CONTAINER") or "").strip()
        or "medi-audio"
    )


def _blob_service_client():
    _require_azure_sdk()
    return BlobServiceClient.from_connection_string(_connection_string())


def _ensure_container(client):
    container_client = client.get_container_client(_container_name())
    try:
        container_client.create_container()
    except Exception:
        pass
    return container_client


def _extract_blob_path(storage_path: str) -> str:
    if not storage_path:
        raise ValueError("Empty storage_path")

    if storage_path.startswith("local:"):
        raise RuntimeError("Local audio storage is disabled")

    if storage_path.startswith("azure:"):
        blob_path = storage_path.replace("azure:", "", 1)
    else:
        # Backward compatibility: allow raw blob path values
        blob_path = storage_path

    blob_path = blob_path.lstrip("/")
    if not blob_path:
        raise ValueError("Invalid storage_path")
    return blob_path


def upload_audio_bytes(
    data: bytes,
    content_type: str,
    filename: str,
    *,
    prefix: str = "voices",
) -> str:
    """
    Returns Azure storage path string: "azure:<blob_path>"
    """
    _require_azure_sdk()

    safe_name = (filename or "voice").replace("/", "_").replace("\\", "_")
    uid = uuid.uuid4().hex
    safe_prefix = (prefix or "voices").strip().strip("/") or "voices"
    blob_path = f"{safe_prefix}/{uid}/{safe_name}"

    client = _blob_service_client()
    container_client = _ensure_container(client)
    blob = container_client.get_blob_client(blob_path)
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
    _require_azure_sdk()
    blob_path = _extract_blob_path(storage_path)

    client = _blob_service_client()
    container_client = client.get_container_client(_container_name())
    blob = container_client.get_blob_client(blob_path)
    return blob.download_blob().readall()


def delete_audio(storage_path: str | None) -> None:
    """
    Best-effort cleanup for temporary uploaded audio blobs.
    """
    if not storage_path:
        return

    try:
        _require_azure_sdk()
        blob_path = _extract_blob_path(storage_path)
        client = _blob_service_client()
        container_client = client.get_container_client(_container_name())
        container_client.get_blob_client(blob_path).delete_blob(delete_snapshots="include")
    except Exception:
        pass


def _connection_parts() -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in _connection_string().split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key] = value
    return parts


def build_blob_read_url(storage_path: str, expiry_seconds: int | None = None) -> str:
    """
    Builds a time-limited SAS read URL for Twilio/web playback.
    """
    _require_azure_sdk()
    if generate_blob_sas is None or BlobSasPermissions is None:
        raise RuntimeError("SAS helpers are unavailable from azure-storage-blob")

    blob_path = _extract_blob_path(storage_path)
    parts = _connection_parts()

    account_name = parts.get("AccountName")
    account_key = parts.get("AccountKey")
    if not account_name or not account_key:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING must include AccountName and AccountKey"
        )

    blob_endpoint = (
        (parts.get("BlobEndpoint") or "").strip()
        or f"https://{account_name}.blob.core.windows.net"
    ).rstrip("/")

    ttl = expiry_seconds
    if ttl is None:
        ttl = int((os.getenv("AZURE_BLOB_URL_TTL_SECONDS") or "86400").strip() or "86400")
    ttl = max(60, ttl)

    sas = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=_container_name(),
        blob_name=blob_path,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )

    return f"{blob_endpoint}/{_container_name()}/{blob_path}?{sas}"
