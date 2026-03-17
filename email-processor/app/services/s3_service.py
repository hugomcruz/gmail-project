"""S3 service — upload files to an S3 bucket using boto3."""

import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Client cache keyed by (region, access_key_id, endpoint_url)
_client_cache: dict[tuple, Any] = {}


def _get_client(
    region: str = "us-east-1",
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    endpoint_url: str | None = None,
) -> Any:
    cache_key = (region, access_key_id, endpoint_url)
    if cache_key not in _client_cache:
        kwargs: dict[str, Any] = {"region_name": region}
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        _client_cache[cache_key] = boto3.client("s3", **kwargs)
    return _client_cache[cache_key]


def upload_bytes(
    data: bytes,
    key: str,
    content_type: str = "application/octet-stream",
    bucket: str = "",
    region: str = "us-east-1",
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    endpoint_url: str | None = None,
    storage_class: str | None = None,
) -> str:
    """
    Upload raw bytes to S3.

    Args:
        data:              Raw file bytes.
        key:               S3 object key (path within the bucket).
        content_type:      MIME type for the stored object.
        bucket:            S3 bucket name (required, from connection config).
        region:            AWS region (default: us-east-1).
        access_key_id:     Explicit AWS access key (optional; uses instance role if omitted).
        secret_access_key: Explicit AWS secret key.
        endpoint_url:      Custom endpoint for S3-compatible providers (e.g. Scaleway, MinIO).
        storage_class:     S3 storage class, e.g. STANDARD, STANDARD_IA, GLACIER.

    Returns:
        The S3 URL of the uploaded object.

    Raises:
        ValueError: If no bucket is provided.
        ClientError / BotoCoreError: On S3 API failure.
    """
    if not bucket:
        raise ValueError(
            "S3 bucket not configured. "
            "Set 'bucket' in the connection definition in connections.yaml."
        )

    client = _get_client(
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        endpoint_url=endpoint_url,
    )

    put_kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
    }
    if storage_class:
        put_kwargs["StorageClass"] = storage_class

    client.put_object(**put_kwargs)

    # Build the URL — use the custom endpoint base when set
    if endpoint_url:
        base = endpoint_url.rstrip("/")
        url = f"{base}/{bucket}/{key}"
    else:
        url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

    logger.debug("S3 upload complete: bucket=%s key=%s size=%d bytes", bucket, key, len(data))
    return url
