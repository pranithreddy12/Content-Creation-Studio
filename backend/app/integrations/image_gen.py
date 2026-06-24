"""Image generation — OpenAI gpt-image-1 primary, Replicate fallback."""
from __future__ import annotations

import base64
import uuid

import httpx
import replicate
from openai import OpenAI

from app.core.config import settings
from app.core.logging import log
from app.utils.storage import s3


def _save_to_s3(brand_id: str, asset_id: str, slot: str, content: bytes, ext: str) -> str:
    key = f"media/{brand_id}/{asset_id}/{slot}-{uuid.uuid4().hex}.{ext}"
    s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=content,
                    ContentType=f"image/{ext}")
    return key


def generate_image(prompt: str, brand_id: str, asset_id: str, slot: str,
                   *, size: str = "1024x1024") -> tuple[str, str]:
    """Returns (storage_key, provider)."""
    if settings.openai_api_key:
        try:
            client = OpenAI(api_key=settings.openai_api_key)
            res = client.images.generate(
                model="gpt-image-1", prompt=prompt[:3500], size=size, n=1,
                response_format="b64_json",
            )
            b64 = res.data[0].b64_json
            png = base64.b64decode(b64)
            return _save_to_s3(brand_id, asset_id, slot, png, "png"), "openai"
        except Exception:
            log.exception("openai_image_failed")
    if settings.replicate_api_token:
        try:
            client = replicate.Client(api_token=settings.replicate_api_token)
            output = client.run(
                "black-forest-labs/flux-schnell",
                input={"prompt": prompt[:3500], "aspect_ratio": "1:1"},
            )
            url = output[0] if isinstance(output, list) else str(output)
            r = httpx.get(url, timeout=60)
            r.raise_for_status()
            return _save_to_s3(brand_id, asset_id, slot, r.content, "png"), "replicate"
        except Exception:
            log.exception("replicate_image_failed")
    raise RuntimeError("no image provider configured or all failed")
