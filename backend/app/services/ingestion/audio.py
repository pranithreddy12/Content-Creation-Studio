"""Audio transcription via OpenAI Whisper."""
from __future__ import annotations

import io
import tempfile

from openai import OpenAI

from app.core.config import settings
from app.utils.storage import s3


def transcribe_audio(storage_key: str) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI(api_key=settings.openai_api_key)
    obj = s3().get_object(Bucket=settings.s3_bucket, Key=storage_key)
    body = obj["Body"].read()
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
        f.write(body)
        f.flush()
        with open(f.name, "rb") as fh:
            resp = client.audio.transcriptions.create(model="whisper-1", file=fh)
    return resp.text or ""
