"""Multi-source research fetchers — every adapter returns a list of normalized dicts.

Normalized item shape:
    {channel, external_id, title, url, excerpt, posted_at (iso str | None),
     engagement: {likes, shares, comments, ...}, meta: {...}}
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

import feedparser
import httpx
import praw

from app.core.config import settings
from app.core.logging import log


# ---------- News (SerpAPI) ----------
def fetch_news(query: str, *, count: int = 20) -> list[dict]:
    if not settings.serpapi_key or not query:
        return []
    try:
        r = httpx.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_news",
                "q": query,
                "api_key": settings.serpapi_key,
                "num": count,
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json().get("news_results", [])
    except Exception:
        log.exception("news_fetch_failed", q=query)
        return []
    out: list[dict] = []
    for it in data[:count]:
        out.append({
            "channel": "news",
            "external_id": it.get("link"),
            "title": it.get("title"),
            "url": it.get("link"),
            "excerpt": it.get("snippet") or "",
            "posted_at": it.get("date"),
            "engagement": {},
            "meta": {"source": it.get("source", {}).get("name")},
        })
    return out


# ---------- Reddit (PRAW) ----------
def fetch_reddit(subreddits: Iterable[str], *, listing: str = "hot", count: int = 20) -> list[dict]:
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        return []
    try:
        reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        reddit.read_only = True
    except Exception:
        return []
    out: list[dict] = []
    for sub in subreddits:
        try:
            iterator = getattr(reddit.subreddit(sub), listing)(limit=count)
            for s in iterator:
                out.append({
                    "channel": "reddit",
                    "external_id": s.id,
                    "title": s.title,
                    "url": f"https://reddit.com{s.permalink}",
                    "excerpt": (s.selftext or "")[:1000],
                    "posted_at": datetime.fromtimestamp(s.created_utc, tz=timezone.utc).isoformat(),
                    "engagement": {"likes": s.score, "comments": s.num_comments},
                    "meta": {"subreddit": sub, "is_question": bool(s.title.endswith("?"))},
                })
        except Exception:
            log.exception("reddit_fetch_failed", sub=sub)
    return out


# ---------- Quora (SerpAPI fallback — no first-class API) ----------
def fetch_quora(query: str, *, count: int = 15) -> list[dict]:
    if not settings.serpapi_key or not query:
        return []
    try:
        r = httpx.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": f"site:quora.com {query}",
                "api_key": settings.serpapi_key,
                "num": count,
            },
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("organic_results", [])[:count]
    except Exception:
        return []
    return [{
        "channel": "quora",
        "external_id": it.get("link"),
        "title": it.get("title"),
        "url": it.get("link"),
        "excerpt": it.get("snippet") or "",
        "posted_at": None,
        "engagement": {},
        "meta": {},
    } for it in results]


# ---------- X / Twitter (Tweepy bearer-token search) ----------
def fetch_x(query: str, *, count: int = 25) -> list[dict]:
    if not settings.x_client_id or not settings.x_client_secret or not query:
        return []
    try:
        import tweepy  # noqa: imported lazily; tweepy installed
        token = _x_app_only_token()
        if not token:
            return []
        client = tweepy.Client(bearer_token=token, wait_on_rate_limit=False)
        resp = client.search_recent_tweets(
            query=query + " -is:retweet lang:en",
            max_results=min(100, max(10, count)),
            tweet_fields=["public_metrics", "created_at", "author_id", "lang"],
        )
        tweets = resp.data or []
    except Exception:
        log.exception("x_fetch_failed", q=query)
        return []
    out: list[dict] = []
    for t in tweets:
        m = t.public_metrics or {}
        out.append({
            "channel": "x",
            "external_id": str(t.id),
            "title": (t.text or "")[:120],
            "url": f"https://x.com/i/web/status/{t.id}",
            "excerpt": t.text or "",
            "posted_at": t.created_at.isoformat() if t.created_at else None,
            "engagement": {
                "likes":    m.get("like_count"),
                "shares":   m.get("retweet_count"),
                "replies":  m.get("reply_count"),
                "quotes":   m.get("quote_count"),
                "views":    m.get("impression_count"),
            },
            "meta": {"author_id": t.author_id},
        })
    return out


def _x_app_only_token() -> str | None:
    import base64
    creds = f"{settings.x_client_id}:{settings.x_client_secret}"
    enc = base64.b64encode(creds.encode()).decode()
    try:
        r = httpx.post(
            "https://api.twitter.com/oauth2/token",
            headers={"Authorization": f"Basic {enc}",
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception:
        return None


# ---------- YouTube (Data API v3) ----------
def fetch_youtube(query: str, *, count: int = 15) -> list[dict]:
    api_key = settings.serpapi_key  # using SerpAPI's youtube engine as a unified path
    if not api_key or not query:
        return []
    try:
        r = httpx.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "youtube",
                "search_query": query,
                "api_key": api_key,
            },
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("video_results", [])[:count]
    except Exception:
        return []
    return [{
        "channel": "youtube",
        "external_id": it.get("link"),
        "title": it.get("title"),
        "url": it.get("link"),
        "excerpt": it.get("description") or "",
        "posted_at": it.get("published_date"),
        "engagement": {"views": _coerce_int(it.get("views"))},
        "meta": {"channel": it.get("channel", {}).get("name")},
    } for it in items]


def _coerce_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    digits = re.sub(r"[^\d]", "", str(v))
    return int(digits) if digits else None


# ---------- Competitor RSS ----------
def fetch_competitor_rss(rss_url: str, *, count: int = 15) -> list[dict]:
    if not rss_url:
        return []
    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return []
    out: list[dict] = []
    for e in feed.entries[:count]:
        out.append({
            "channel": "competitor",
            "external_id": e.get("id") or e.get("link"),
            "title": e.get("title"),
            "url": e.get("link"),
            "excerpt": _strip_html(e.get("summary") or "")[:800],
            "posted_at": e.get("published"),
            "engagement": {},
            "meta": {"source": rss_url},
        })
    return out


HTML_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return HTML_RE.sub("", s or "")
