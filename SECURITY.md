# Security Policy

## Reporting a Vulnerability

Email `security@studio.example.com` with reproduction steps. Expect
acknowledgement within 24 h and a fix or status update within 7 d.

Please do **not** open public issues for security problems.

## Threat Model Summary

| Layer            | Controls                                                          |
|------------------|-------------------------------------------------------------------|
| Edge             | TLS 1.3 only · HSTS · NGINX-Ingress rate limit · WAF              |
| Auth             | Clerk JWT (RS256) verified per request · org-scoped RBAC          |
| API              | CORS allowlist · CSRF-free (JWT bearer) · Pydantic input validation · per-IP + per-account rate limit |
| Workers          | No host net access in prod · signed S3 URLs · least-priv IAM      |
| Data at rest     | RDS AES-256 · S3 SSE-S3 · Qdrant encrypted EBS                    |
| Secrets          | Kubernetes Secrets (sealed) · AES-GCM for OAuth blobs in DB · KMS-managed master key |
| Audit            | `audit_log` table append-only · ship to Loki                      |
| Supply chain     | Trivy + Gitleaks in CI · Dependabot · signed images (cosign) · provenance + SBOM in build-deploy.yml |

## Required Headers (set in `SecurityHeadersMiddleware`)

- `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), camera=(), microphone=()`
- `Content-Security-Policy: …` (see middleware)

## Data Handling

- PII (email, name) lives in `users` only; never logged.
- Brand `style_guide` may contain user-uploaded text; flagged for moderation.
- All publishes recorded in `audit_log` with `external_id` for takedown.
- 30-day retention for raw `research_items`; aggregates kept forever.

## Operational

- Rotate Clerk JWT signing keys every 90 d.
- Rotate Stripe restricted keys every 90 d.
- AES-GCM master key (`SECRET_KEY`) rotation requires DEK re-wrap migration.
- Stripe + platform webhooks must verify signatures before any DB write.
- Quarterly access reviews of GitHub, AWS, Clerk admin, Stripe.
