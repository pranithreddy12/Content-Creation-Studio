"""Polymorphic source extractor — pulls clean text + metadata from any input kind.

Supported kinds:
  - topic        : free text seed; returned as-is.
  - url / blog   : HTML page, cleaned via trafilatura.
  - youtube      : transcript via youtube_transcript_api; falls back to oEmbed title.
  - pdf          : binary stream from S3.
  - voice        : audio in S3 → Whisper transcript (handled in audio.py).
  - competitor   : crawls homepage + sitemap top-N.
  - product      : same as url, but tagged differently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from pypdf import PdfReader
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from app.services.ingestion.audio import transcribe_audio
from app.utils.storage import s3
from app.core.config import settings


@dataclass
class Extracted:
    title: Optional[str]
    text: str
    meta: dict


def _youtube_id(url: str) -> Optional[str]:
    p = urlparse(url)
    if "youtu.be" in p.netloc:
        return p.path.lstrip("/")
    if "youtube.com" in p.netloc:
        if p.path == "/watch":
            from urllib.parse import parse_qs
            return parse_qs(p.query).get("v", [None])[0]
        if p.path.startswith("/shorts/"):
            return p.path.split("/")[2]
    return None


def _extract_url(url: str) -> Extracted:
    downloaded = trafilatura.fetch_url(url, no_ssl=True)
    if not downloaded:
        raise ValueError(f"failed to fetch {url}")
    text = trafilatura.extract(
        downloaded, include_comments=False, include_tables=True, deduplicate=True
    ) or ""
    md = trafilatura.metadata.extract_metadata(downloaded)
    title = md.title if md else None
    meta = {"url": url, "author": md.author if md else None, "date": md.date if md else None}
    return Extracted(title=title, text=text, meta=meta)


def _extract_youtube(url: str) -> Extracted:
    vid = _youtube_id(url)
    if not vid:
        raise ValueError(f"not a youtube url: {url}")
    text = ""
    try:
        segs = YouTubeTranscriptApi.get_transcript(vid, languages=["en", "en-US", "en-GB"])
        text = " ".join(s["text"] for s in segs)
    except (TranscriptsDisabled, NoTranscriptFound):
        pass
    title = None
    try:
        oe = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=10,
        ).json()
        title = oe.get("title")
    except Exception:
        pass
    return Extracted(title=title, text=text, meta={"url": url, "video_id": vid})


def _extract_pdf(storage_key: str) -> Extracted:
    obj = s3().get_object(Bucket=settings.s3_bucket, Key=storage_key)
    body = obj["Body"].read()
    import io
    reader = PdfReader(io.BytesIO(body))
    pages = [p.extract_text() or "" for p in reader.pages]
    text = "\n\n".join(pages).strip()
    title = (reader.metadata.title if reader.metadata else None) or storage_key.rsplit("/", 1)[-1]
    return Extracted(title=title, text=text, meta={"storage_key": storage_key, "pages": len(pages)})


def _extract_voice(storage_key: str) -> Extracted:
    text = transcribe_audio(storage_key)
    return Extracted(title=None, text=text, meta={"storage_key": storage_key, "transcribed": True})


def _extract_competitor(url: str) -> Extracted:
    home = _extract_url(url)
    pages = [home.text]
    try:
        sm = httpx.get(url.rstrip("/") + "/sitemap.xml", timeout=10)
        if sm.status_code == 200:
            from xml.etree import ElementTree as ET
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            root = ET.fromstring(sm.text)
            locs = [el.text for el in root.findall(".//s:url/s:loc", ns)][:5]
            for loc in locs:
                try:
                    pages.append(_extract_url(loc).text)
                except Exception:
                    continue
    except Exception:
        pass
    return Extracted(title=home.title, text="\n\n".join(filter(None, pages)), meta={"url": url, "kind": "competitor"})


def extract(kind: str, *, url: str | None = None, text: str | None = None, storage_key: str | None = None) -> Extracted:
    kind = kind.lower()
    if kind == "topic":
        return Extracted(title=None, text=(text or "").strip(), meta={"kind": "topic"})
    if kind in {"url", "blog", "product"}:
        if not url:
            raise ValueError("url required")
        return _extract_url(url)
    if kind == "youtube":
        if not url:
            raise ValueError("url required")
        return _extract_youtube(url)
    if kind == "pdf":
        if not storage_key:
            raise ValueError("storage_key required")
        return _extract_pdf(storage_key)
    if kind == "voice":
        if not storage_key:
            raise ValueError("storage_key required")
        return _extract_voice(storage_key)
    if kind == "competitor":
        if not url:
            raise ValueError("url required")
        return _extract_competitor(url)
    raise ValueError(f"unsupported kind: {kind}")
