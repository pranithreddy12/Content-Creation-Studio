"""Integration adapters registry.

Each platform package exposes a `publisher` and (optionally) `analytics` module
implementing:

    async def publish(channel, asset) -> dict   # returns {id, url}
    async def fetch_metrics(schedule) -> dict | None

The registry maps `platform` string → module so the dispatcher can lookup
adapters without import cycles.
"""
from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Optional

PLATFORM_TO_MODULE = {
    "wordpress": "app.integrations.wordpress.publisher",
    "linkedin":  "app.integrations.linkedin.publisher",
    "x":         "app.integrations.twitter.publisher",
    "facebook":  "app.integrations.facebook.publisher",
    "instagram": "app.integrations.instagram.publisher",
    "tiktok":    "app.integrations.tiktok.publisher",
    "youtube":   "app.integrations.youtube.publisher",
    "reddit":    "app.integrations.reddit.publisher",
    "quora":     "app.integrations.quora.publisher",
    "email":     "app.integrations.email.publisher",
}


class _Registry:
    def __init__(self) -> None:
        self._cache: dict[str, ModuleType] = {}

    def get(self, platform: Optional[str]) -> ModuleType | None:
        if not platform:
            return None
        if platform in self._cache:
            return self._cache[platform]
        path = PLATFORM_TO_MODULE.get(platform)
        if not path:
            return None
        try:
            mod = import_module(path)
            self._cache[platform] = mod
            return mod
        except Exception:
            return None


publish_registry = _Registry()
