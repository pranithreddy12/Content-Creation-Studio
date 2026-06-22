# Implementation Roadmap

Module-by-module build order. Each iteration of the /loop run completes one or more checkboxes.

## M0 — Foundations
- [x] Monorepo folder structure
- [x] Architecture doc
- [x] Database schema (Postgres + Qdrant)
- [x] Roadmap
- [x] Root `docker-compose.yml`
- [x] Root `.env.example`
- [x] CI skeleton

## M1 — Backend Core
- [ ] FastAPI app skeleton + settings
- [ ] Clerk JWT auth middleware
- [ ] SQLAlchemy + Alembic migrations
- [ ] Postgres models (accounts, workspaces, brands, users, RBAC)
- [ ] Redis cache + rate-limit
- [ ] Healthchecks, structured logging, Prometheus middleware

## M2 — Domain Models
- [ ] Brand memory model + CRUD
- [ ] Source ingestion model (URL/PDF/voice/video upload)
- [ ] Ingest pipeline (extractor → chunker → embedder → Qdrant)
- [ ] Content idea, asset, render, schedule models

## M3 — AI Agent System
- [ ] Agent base class + Context + Result
- [ ] LLM router (OpenAI / Anthropic / Gemini fallback)
- [ ] Prompt registry with versioning
- [ ] Research, Strategist, Writer, SEO, Designer, Video, Publisher, Analytics, Learning agents
- [ ] Agent run persistence + replay

## M4 — Content Loop
- [ ] Celery + beat config
- [ ] Daily loop chain: research → ideas → score → select → assets → media → video → SEO → publish → analytics → learning
- [ ] Idempotency + retry policies
- [ ] Per-brand TZ-aware scheduling

## M5 — Viral Engine
- [ ] Per-platform viral crawlers
- [ ] Pattern extractor (hook/structure/CTA/emotion)
- [ ] Voyage embeddings → Qdrant `viral_patterns`
- [ ] RAG hook into writer + video agents

## M6 — Publishing Integrations
- [ ] OAuth handshakes for LI, X, IG, FB, TikTok, YT, WordPress
- [ ] Per-platform publisher adapters
- [ ] Media upload + scheduled publish
- [ ] Webhook ingest for engagement events

## M7 — Media + Video
- [ ] Image generation adapter (OpenAI / Gemini / Replicate)
- [ ] TTS adapter (ElevenLabs)
- [ ] Caption + b-roll script generation
- [ ] Render service (Remotion or ffmpeg + composer)

## M8 — Frontend Web (Next.js 15)
- [ ] App-Router scaffold, Clerk middleware, Shadcn UI install
- [ ] Auth + onboarding wizard
- [ ] Brand setup + source ingestion UI
- [ ] Content calendar (drag & drop, week/month)
- [ ] Asset review/approval queue
- [ ] Workflow visual builder (nodes/edges)
- [ ] Analytics dashboards

## M9 — Mobile (Expo)
- [ ] Expo + RN scaffold + Clerk Expo
- [ ] Approvals + Push notifications
- [ ] Analytics summary
- [ ] AI chat assistant

## M10 — Workflow Engine
- [ ] Workflow def schema + validator
- [ ] Workflow runner (trigger/schedule/event/condition/loop/approval)
- [ ] Visual builder ↔ JSON round-trip

## M11 — Billing
- [ ] Stripe products + webhooks
- [ ] Usage metering (assets generated, video minutes, publish ops)
- [ ] Plan enforcement middleware

## M12 — Infra / Deploy
- [ ] Dockerfiles per service
- [ ] Helm charts + Kustomize overlays (dev/stage/prod)
- [ ] HPA, PDB, NetworkPolicy
- [ ] Terraform: VPC, RDS, ElastiCache, S3, EKS
- [ ] GitHub Actions: lint, test, build, push, deploy

## M13 — Observability
- [x] Prometheus + Grafana dashboards (system, content-loop, cost)
- [x] Loki + Promtail
- [x] Alertmanager rules (api/workers/db/cost/business)
- [x] PostHog + Sentry integration (already wired in app)
- [x] Business metrics: agent runs, LLM cost, publish outcomes, ingest

## M14 — Testing & Hardening
- [x] Unit (pytest backend, vitest frontend)
- [x] e2e Playwright config + landing smoke spec
- [x] Load tests (Locust)
- [x] Security headers middleware (CSP/HSTS/X-Frame/...)
- [x] Audit logging service
- [x] CodeQL + Trivy + Gitleaks + Dependabot in CI
- [x] SECURITY.md threat model + reporting policy
