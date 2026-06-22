# System Architecture

## 1. High-Level Topology

```
                   ┌─────────────────────────────────────────────┐
                   │                  Clients                    │
                   │   Next.js Web   |   React Native Mobile     │
                   └────────────┬────────────────┬───────────────┘
                                │                │
                       Clerk JWT│       Clerk JWT│
                                ▼                ▼
                   ┌─────────────────────────────────────────────┐
                   │             API Gateway (NGINX)             │
                   └────────────┬────────────────────────────────┘
                                │
                                ▼
       ┌───────────────────────────────────────────────────────────────┐
       │                   FastAPI Application Tier                    │
       │   /v1/auth  /v1/brands  /v1/content  /v1/agents  /v1/workflows│
       │   /v1/analytics  /v1/publishing  /v1/calendar  /v1/webhooks   │
       └───┬──────────────────┬──────────────┬──────────────┬──────────┘
           │                  │              │              │
           ▼                  ▼              ▼              ▼
     ┌───────────┐      ┌──────────┐   ┌──────────┐   ┌──────────────┐
     │ Postgres  │      │  Redis   │   │  Qdrant  │   │     S3       │
     │  (RW + RR)│      │ (cache + │   │ (vectors)│   │ (media/blobs)│
     └─────┬─────┘      │  broker) │   └────┬─────┘   └──────┬───────┘
           │            └────┬─────┘        │                │
           │                 │              │                │
           ▼                 ▼              ▼                ▼
     ┌────────────────────────────────────────────────────────────────┐
     │                Celery Workers (queues by domain)               │
     │   research / writing / video / publishing / analytics / heavy  │
     └───┬─────────────┬───────────────┬────────────────┬─────────────┘
         ▼             ▼               ▼                ▼
   ┌──────────┐  ┌──────────┐    ┌──────────┐     ┌──────────────┐
   │ LLM APIs │  │ Social   │    │ Render   │     │ SEO/Scraper  │
   │ (OpenAI, │  │ APIs     │    │ Service  │     │ Workers      │
   │  Claude, │  │ (LI, X,  │    │ (ffmpeg, │     │ (SerpAPI,    │
   │  Gemini) │  │  IG,...) │    │  remotion│     │  Reddit,...) │
   └──────────┘  └──────────┘    └──────────┘     └──────────────┘
```

## 2. Service Boundaries

| Service           | Responsibility                                          | Scaling Knob |
|-------------------|---------------------------------------------------------|--------------|
| `api`             | HTTP REST + WebSocket, request validation, RBAC         | HPA on RPS   |
| `worker-research` | Trends / Reddit / Quora / X / YT crawl + summarize      | Queue depth  |
| `worker-writing`  | LLM blog / post / email / script generation             | Queue depth  |
| `worker-video`    | TTS, captions, b-roll, ffmpeg / Remotion render         | GPU pool     |
| `worker-publish`  | Posts to LinkedIn / X / WP / IG / TikTok / YT / FB     | Queue depth  |
| `worker-analytics`| Pulls platform metrics, attribution, learning loop      | Cron + queue |
| `beat`            | Celery beat for daily loop kickoff + housekeeping       | Singleton    |
| `embedder`        | Generates Voyage/OpenAI embeddings, upserts to Qdrant   | Queue depth  |
| `agent-router`    | Routes agent step → correct worker queue                | Stateless    |

## 3. Multi-Tenancy

- **Account** → **Workspaces** → **Brands** → **Resources**
- Postgres row-level isolation via `account_id` + `brand_id` on every domain table.
- Per-brand Qdrant collection: `brand_{id}__assets`, `brand_{id}__viral_patterns`.
- Per-brand S3 prefix: `s3://studio-media/{account_id}/{brand_id}/...`.
- Agency mode: a single Clerk org owns N brands and N teams; RBAC enforces `brand:read / brand:write / brand:publish`.

## 4. AI Agent System

Agents are graph-structured. Each agent is a stateless Python class that consumes a `Context` (brand memory, retrieved viral patterns, prior step output) and emits a typed `AgentResult`.

```
ResearchAgent ─▶ StrategistAgent ─▶ WriterAgent ─▶ SEOAgent ─▶ DesignerAgent
                                          │                         │
                                          └──▶ VideoAgent ◀──────────┘
                                                       │
                                                       ▼
                                                PublisherAgent
                                                       │
                                                       ▼
                                               AnalyticsAgent
                                                       │
                                                       ▼
                                                LearningAgent
```

Each agent run is persisted as an `agent_run` row with: `input_hash`, `prompt_version`, `model`, `tokens_in/out`, `cost_usd`, `latency_ms`, `output_json`, `error`. This is the substrate the Learning Agent reads from.

## 5. The Daily Content Loop

Triggered by Celery Beat per brand at the brand's configured TZ + hour:

1. `research.run(brand_id)` — pulls news, Reddit, Quora, X, YT, competitor RSS.
2. `opportunities.extract(research_id)` — popular questions, viral formats, keywords.
3. `ideas.generate(brand_id, n=100)` — LLM brainstorm with retrieved viral patterns.
4. `ideas.score(ids)` — composite score from search volume, trend velocity, competition, predicted engagement.
5. `selector.pick_top(brand_id, k=brand.daily_quota)` — top-K by score, dedupe vs `published_index`.
6. `assets.generate(idea_id)` — fan out to blog / LI / X-thread / IG / carousel / email / reel / YT script.
7. `media.generate(idea_id)` — image prompts → image gen, infographics, thumbnails.
8. `video.render(idea_id)` — script → TTS → b-roll → captions → ffmpeg/Remotion.
9. `seo.optimize(asset_id)` — title, meta, internal links, JSON-LD schema.
10. `publisher.dispatch(asset_id)` — per-channel API push at scheduled time.
11. `analytics.collect(asset_id, +24h, +7d, +30d)` — pulls platform metrics.
12. `learning.update(brand_id)` — updates `pattern_scores` and tunes prompts.

## 6. Viral Content Engine

- Crawl viral posts via per-platform jobs; store raw + parsed.
- Run extractors (hook, structure, CTA, emotional triggers) via LLM with strict JSON.
- Embed each pattern via Voyage; upsert to Qdrant `viral_patterns` collection.
- During writing, agent RAG-retrieves top-K matching patterns to ground generation.

## 7. Workflow Engine

A visual DAG editor (frontend) compiles to a `workflow_def` JSON. Backend `workflow_runner` executes nodes (trigger / schedule / agent / webhook / condition / loop / human-approval). Persisted as `workflow_runs` with per-node status.

## 8. Security

- Clerk-issued JWT verified on every request.
- Per-resource ABAC enforced in `deps.require_brand_access`.
- All external API keys encrypted at rest with `AES-256-GCM` using KMS-derived DEK.
- Egress allowlist for workers; signed URLs for S3 reads.
- Rate-limit by account tier (Redis token bucket).
- Audit log table for every publish, key change, and webhook write.

## 9. Observability

- Prometheus: per-endpoint RPS / p95, queue depth, worker concurrency, LLM cost gauge.
- Grafana dashboards: `system`, `content-loop`, `cost`, `publishing`.
- Loki: structured JSON logs with `trace_id`, `account_id`, `brand_id`, `agent_run_id`.
- PostHog: product analytics + feature flags.
- Sentry: exceptions + perf traces.

## 10. Scale Targets

- 100k MAU, 10M generated assets, 5 regions.
- Read replicas + PgBouncer for Postgres.
- Redis cluster (6 nodes), Qdrant cluster (3 nodes, replication factor 2).
- Workers autoscale on queue depth; video workers on GPU pool size.
- CDN in front of S3 for asset delivery.
