"""GCS backend — upload encrypted shell to public bucket, stable URL.

Content is encrypted client-side (see encryption_layer.py) so the public
bucket is safe to serve the ciphertext. The URL is stable (bucket + path
slug are user-chosen), bookmarkable, home-screen-icon-able.

Requires GOOGLE_APPLICATION_CREDENTIALS in .env pointing at a service
account JSON with storage.objectAdmin on the configured bucket.
"""
from __future__ import annotations

import logging
import os

from . import WebUIBackend

log = logging.getLogger("monogram.webui.gcs")


class GCSBackend(WebUIBackend):
    def __init__(self) -> None:
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            # Validate creds BEFORE importing the heavy dep — gives clearer errors.
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
            if not creds_path or not os.path.exists(creds_path):
                raise RuntimeError(
                    "GOOGLE_APPLICATION_CREDENTIALS not set or file missing. "
                    "See docs/setup/gcp-webui.md."
                )
            try:
                from google.cloud import storage  # lazy import — heavy dep
            except ImportError as e:
                raise RuntimeError(
                    "google-cloud-storage not installed. "
                    "Install with: pip install google-cloud-storage"
                ) from e
            self._client = storage.Client()
        return self._client

    def _bucket_and_path(self) -> tuple[str, str]:
        from ..vault_config import load_vault_config
        vcfg = load_vault_config()
        bucket = (vcfg.webui_gcs or {}).get("bucket", "").strip()
        slug = (vcfg.webui_gcs or {}).get("path_slug", "main").strip() or "main"
        if not bucket:
            raise RuntimeError(
                "webui_gcs.bucket not set in mono/config.md — "
                "run /config_webui_gcs_bucket <name>"
            )
        return bucket, f"{slug}/index.html"

    async def publish(self, encrypted_html: bytes) -> str:
        import asyncio

        bucket_name, object_path = self._bucket_and_path()

        def _upload():
            client = self._get_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_path)
            blob.cache_control = "no-cache, no-store, must-revalidate"
            blob.upload_from_string(encrypted_html, content_type="text/html")
            return f"https://storage.googleapis.com/{bucket_name}/{object_path}"

        return await asyncio.get_event_loop().run_in_executor(None, _upload)

    async def current_url(self) -> str | None:
        try:
            bucket, path = self._bucket_and_path()
            return f"https://storage.googleapis.com/{bucket}/{path}"
        except Exception:
            return None

    async def teardown(self) -> None:
        self._client = None
