"""Business metrics — counters and histograms exposed at /metrics.

Imports must be side-effect free. Import this module once from app.main so the
metric registries get populated.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# --- Content loop ---
DAILY_LOOP_KICKOFFS = Counter(
    "daily_loop_kickoffs_total", "Daily loops fired", ["brand_id"]
)
IDEAS_GENERATED = Counter(
    "ideas_generated_total", "Content ideas generated", ["brand_id"]
)
ASSETS_GENERATED = Counter(
    "assets_generated_total", "Content assets generated", ["brand_id", "format"]
)
VIDEO_RENDERS = Counter(
    "video_renders_total", "Video renders completed", ["status"]
)

# --- Publishing ---
PUBLISH_ATTEMPTS = Counter(
    "publish_attempts_total", "Publish attempts", ["platform", "outcome"]
)
PUBLISH_LATENCY = Histogram(
    "publish_latency_seconds", "Publish call latency", ["platform"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60),
)

# --- Agents / LLM ---
AGENT_RUNS = Counter(
    "agent_runs_total", "Agent invocations", ["agent", "status"]
)
LLM_COST = Counter(
    "llm_cost_usd_total", "LLM cost in USD", ["provider", "model", "agent"]
)
LLM_TOKENS = Counter(
    "llm_tokens_total", "LLM tokens", ["provider", "model", "direction"]
)

# --- Ingest ---
INGEST_RUNS = Counter("ingest_runs_total", "Source ingest runs", ["kind", "status"])
INGEST_CHUNKS = Counter("ingest_chunks_total", "Chunks embedded", ["brand_id"])
