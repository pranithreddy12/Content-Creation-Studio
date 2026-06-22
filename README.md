# AI Content Creation Studio

Autonomous omnichannel content engine. One input → blogs, social, video scripts, ads, emails, and auto-publishing — with a self-improving feedback loop.

## Monorepo Layout

```
.
├── backend/            FastAPI + Celery + AI agents
├── frontend/           Next.js 15 web app
├── mobile/             React Native (Expo) iOS + Android
├── ai-agents/          Reusable agent prompts / chains
├── workers/            Celery worker entrypoints
├── infrastructure/     Docker, K8s, Terraform, monitoring
├── shared/             Cross-language schema (OpenAPI, JSON Schemas)
├── docs/               Architecture, roadmap, runbooks
├── scripts/            Dev / CI helpers
└── tests/              Cross-service e2e tests
```

## Stack

| Layer        | Tech |
|--------------|------|
| Web          | Next.js 15, TypeScript, Tailwind, Shadcn UI, Framer Motion |
| Mobile       | React Native, Expo |
| Backend API  | FastAPI (Python 3.12) |
| Queue        | Celery + Redis |
| DB           | PostgreSQL 16, Redis 7 |
| Vector       | Qdrant |
| Storage      | S3 (R2 / MinIO compatible) |
| LLMs         | OpenAI, Anthropic Claude, Google Gemini |
| Embeddings   | Voyage, OpenAI |
| Auth         | Clerk |
| Payments     | Stripe |
| Deploy       | Docker, Kubernetes (Helm), Terraform |
| Observability| Prometheus, Grafana, Loki, PostHog |

## Quickstart

```bash
cp .env.example .env
docker compose up -d postgres redis qdrant minio
docker compose up -d backend worker beat frontend
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).
