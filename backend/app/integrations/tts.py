"""Text-to-speech adapter — ElevenLabs primary, OpenAI fallback."""
from __future__ import annotations

import uuid

from elevenlabs.client import ElevenLabs
from openai import OpenAI

from app.core.config import settings
from app.core.logging import log
from app.utils.storage import s3

DEFAULT_VOICE_EL = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"
DEFAULT_VOICE_OAI = "alloy"


def _save(brand_id: str, asset_id: str, content: bytes, ext: str = "mp3") -> str:
    key = f"media/{brand_id}/{asset_id}/voice-{uuid.uuid4().hex}.{ext}"
    s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=content,
                    ContentType=f"audio/{ext}")
    return key


def synthesize(text: str, brand_id: str, asset_id: str, voice: str | None = None) -> tuple[str, str]:
    if settings.elevenlabs_api_key:
        try:
            client = ElevenLabs(api_key=settings.elevenlabs_api_key)
            audio = client.text_to_speech.convert(
                voice_id=voice or DEFAULT_VOICE_EL,
                model_id="eleven_turbo_v2_5",
                text=text[:5000],
                output_format="mp3_44100_128",
            )
            buf = b"".join(audio)
            return _save(brand_id, asset_id, buf), "elevenlabs"
        except Exception:
            log.exception("elevenlabs_tts_failed")
    if settings.openai_api_key:
        try:
            client = OpenAI(api_key=settings.openai_api_key)
            r = client.audio.speech.create(
                model="tts-1-hd",
                voice=voice or DEFAULT_VOICE_OAI,
                input=text[:4096],
                response_format="mp3",
            )
            return _save(brand_id, asset_id, r.content), "openai"
        except Exception:
            log.exception("openai_tts_failed")
    raise RuntimeError("no tts provider configured")
