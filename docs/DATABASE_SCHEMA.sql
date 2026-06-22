-- ============================================================================
-- AI Content Creation Studio — Postgres Schema
-- Convention:
--   * UUID PKs everywhere
--   * Every domain row carries account_id (+ brand_id where applicable) for RLS
--   * created_at / updated_at on all tables
--   * Soft delete via deleted_at where useful
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";  -- pgvector for small inline embeds; large ones live in Qdrant

-- ---------------------------------------------------------------------------
-- Identity & Tenancy
-- ---------------------------------------------------------------------------

CREATE TABLE accounts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_org_id    TEXT UNIQUE NOT NULL,
  name            TEXT NOT NULL,
  plan            TEXT NOT NULL DEFAULT 'free',                -- free|pro|agency|enterprise
  stripe_customer TEXT,
  region          TEXT NOT NULL DEFAULT 'us-east-1',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_user_id TEXT UNIQUE NOT NULL,
  email         TEXT NOT NULL,
  name          TEXT,
  avatar_url    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE account_members (
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL,                                   -- owner|admin|editor|viewer
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_id, user_id)
);

CREATE TABLE workspaces (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE brands (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL,
  description     TEXT,
  website_url     TEXT,
  product_url     TEXT,
  competitor_urls TEXT[] DEFAULT '{}',
  primary_topic   TEXT,
  audience        TEXT,
  tone            TEXT,                                        -- friendly|expert|witty|...
  style_guide     JSONB DEFAULT '{}'::jsonb,                   -- voice, banned_words, examples
  messaging       JSONB DEFAULT '{}'::jsonb,                   -- value props, positioning
  daily_quota     INT NOT NULL DEFAULT 1,
  timezone        TEXT NOT NULL DEFAULT 'UTC',
  publish_window  JSONB DEFAULT '{"start":"09:00","end":"18:00"}'::jsonb,
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, slug)
);
CREATE INDEX ON brands (account_id);
CREATE INDEX ON brands (workspace_id);

-- ---------------------------------------------------------------------------
-- Source Material (the "single input")
-- ---------------------------------------------------------------------------

CREATE TABLE sources (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id   UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id     UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  kind         TEXT NOT NULL,                                  -- topic|url|youtube|blog|pdf|voice|competitor|product
  title        TEXT,
  raw_text     TEXT,
  url          TEXT,
  storage_key  TEXT,                                           -- S3 key for files
  meta         JSONB DEFAULT '{}'::jsonb,
  status       TEXT NOT NULL DEFAULT 'pending',                -- pending|extracting|embedded|failed
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON sources (brand_id, kind);
CREATE INDEX ON sources (status);

CREATE TABLE source_chunks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id    UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  brand_id     UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  ord          INT  NOT NULL,
  text         TEXT NOT NULL,
  qdrant_id    TEXT,                                           -- mirror id in Qdrant
  tokens       INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON source_chunks (source_id, ord);
CREATE INDEX ON source_chunks (brand_id);

-- ---------------------------------------------------------------------------
-- Research & Opportunities
-- ---------------------------------------------------------------------------

CREATE TABLE research_runs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand_id    UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status      TEXT NOT NULL DEFAULT 'running',
  meta        JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE research_items (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  research_id   UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  brand_id      UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  channel       TEXT NOT NULL,                                 -- news|reddit|quora|x|youtube|competitor|trends
  external_id   TEXT,
  title         TEXT,
  url           TEXT,
  excerpt       TEXT,
  posted_at     TIMESTAMPTZ,
  engagement    JSONB DEFAULT '{}'::jsonb,
  meta          JSONB DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON research_items (brand_id, channel);
CREATE INDEX ON research_items (research_id);

CREATE TABLE opportunities (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand_id        UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  research_id     UUID REFERENCES research_runs(id) ON DELETE SET NULL,
  kind            TEXT NOT NULL,                               -- question|trend|format|keyword
  text            TEXT NOT NULL,
  score           NUMERIC(6,3) DEFAULT 0,
  attrs           JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON opportunities (brand_id, kind);

-- ---------------------------------------------------------------------------
-- Content Ideas, Assets, Renders, Schedule, Publish
-- ---------------------------------------------------------------------------

CREATE TABLE content_ideas (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id       UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  research_id    UUID REFERENCES research_runs(id) ON DELETE SET NULL,
  title          TEXT NOT NULL,
  angle          TEXT,
  keywords       TEXT[] DEFAULT '{}',
  audience       TEXT,
  format_hints   TEXT[] DEFAULT '{}',                          -- blog,twitter_thread,reel,...
  search_volume  INT,
  trend_velocity NUMERIC(6,3),
  competition    NUMERIC(6,3),
  engagement_est NUMERIC(6,3),
  composite_score NUMERIC(6,3),
  status         TEXT NOT NULL DEFAULT 'new',                  -- new|selected|generated|published|rejected
  selected_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON content_ideas (brand_id, status);
CREATE INDEX ON content_ideas (brand_id, composite_score DESC);

CREATE TABLE content_assets (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id       UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  idea_id        UUID NOT NULL REFERENCES content_ideas(id) ON DELETE CASCADE,
  format         TEXT NOT NULL,                                -- blog|linkedin|x_thread|instagram|carousel|reel|short|tiktok|email_newsletter|sales_email|landing|ad|facebook|reddit|quora|yt_script
  title          TEXT,
  body           TEXT,                                         -- canonical markdown / json depending on format
  body_json      JSONB,                                        -- structured per-format payload (slides, tweets[], shots[])
  word_count     INT,
  seo            JSONB DEFAULT '{}'::jsonb,                    -- title,meta,slug,schema,internal_links
  status         TEXT NOT NULL DEFAULT 'draft',                -- draft|review|approved|scheduled|published|failed
  approval_state JSONB DEFAULT '{}'::jsonb,                    -- approver, comments
  generated_by   UUID,                                         -- agent_run id
  parent_asset_id UUID REFERENCES content_assets(id) ON DELETE SET NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON content_assets (brand_id, format, status);
CREATE INDEX ON content_assets (idea_id);

CREATE TABLE media_assets (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id       UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  asset_id       UUID REFERENCES content_assets(id)   ON DELETE CASCADE,
  kind           TEXT NOT NULL,                                -- image|infographic|thumbnail|social_graphic|broll_clip|tts_audio|video
  storage_key    TEXT NOT NULL,
  mime_type      TEXT,
  width          INT,
  height         INT,
  duration_sec   NUMERIC(8,2),
  prompt         TEXT,
  provider       TEXT,
  meta           JSONB DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON media_assets (asset_id);
CREATE INDEX ON media_assets (brand_id, kind);

CREATE TABLE video_renders (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id       UUID NOT NULL REFERENCES content_assets(id) ON DELETE CASCADE,
  brand_id       UUID NOT NULL REFERENCES brands(id)         ON DELETE CASCADE,
  format         TEXT NOT NULL,                               -- reel|short|tiktok|yt_long
  script_json    JSONB NOT NULL,
  storyboard     JSONB,
  storage_key    TEXT,
  duration_sec   NUMERIC(8,2),
  status         TEXT NOT NULL DEFAULT 'queued',              -- queued|rendering|done|failed
  error          TEXT,
  cost_usd       NUMERIC(10,4),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ
);
CREATE INDEX ON video_renders (status);

-- ---------------------------------------------------------------------------
-- Publishing
-- ---------------------------------------------------------------------------

CREATE TABLE publish_channels (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id    UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id      UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  platform      TEXT NOT NULL,                                -- linkedin|x|wordpress|instagram|tiktok|youtube|facebook|reddit|quora|medium|email
  display_name  TEXT,
  oauth_blob    JSONB NOT NULL,                               -- encrypted tokens
  meta          JSONB DEFAULT '{}'::jsonb,
  status        TEXT NOT NULL DEFAULT 'connected',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (brand_id, platform, display_name)
);

CREATE TABLE schedules (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id       UUID NOT NULL REFERENCES brands(id)  ON DELETE CASCADE,
  asset_id       UUID NOT NULL REFERENCES content_assets(id) ON DELETE CASCADE,
  channel_id     UUID NOT NULL REFERENCES publish_channels(id) ON DELETE CASCADE,
  scheduled_at   TIMESTAMPTZ NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending',             -- pending|publishing|published|failed|cancelled
  attempt        INT NOT NULL DEFAULT 0,
  external_id    TEXT,
  external_url   TEXT,
  error          TEXT,
  published_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON schedules (status, scheduled_at);
CREATE INDEX ON schedules (brand_id, scheduled_at);

-- ---------------------------------------------------------------------------
-- Analytics & Learning
-- ---------------------------------------------------------------------------

CREATE TABLE asset_metrics (
  id           BIGSERIAL PRIMARY KEY,
  asset_id     UUID NOT NULL REFERENCES content_assets(id) ON DELETE CASCADE,
  brand_id     UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  platform     TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  views        BIGINT,
  clicks       BIGINT,
  shares       BIGINT,
  saves        BIGINT,
  comments     BIGINT,
  likes        BIGINT,
  watch_time_s BIGINT,
  ctr          NUMERIC(8,5),
  meta         JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX ON asset_metrics (asset_id, collected_at);
CREATE INDEX ON asset_metrics (brand_id, platform, collected_at);

CREATE TABLE pattern_scores (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand_id    UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  pattern_key TEXT NOT NULL,                                  -- hook_type|structure|cta_style|emotion
  pattern_val TEXT NOT NULL,
  ema_score   NUMERIC(8,4) NOT NULL DEFAULT 0,                -- learning rate updated EMA
  sample_n    INT NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (brand_id, pattern_key, pattern_val)
);

-- ---------------------------------------------------------------------------
-- Viral Engine
-- ---------------------------------------------------------------------------

CREATE TABLE viral_posts (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform       TEXT NOT NULL,                               -- x|linkedin|reel|short|tiktok|reddit
  external_id    TEXT,
  url            TEXT,
  author         TEXT,
  raw            TEXT,
  metrics        JSONB DEFAULT '{}'::jsonb,
  posted_at      TIMESTAMPTZ,
  crawled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  hash           TEXT,
  UNIQUE (platform, external_id)
);

CREATE TABLE viral_patterns (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  viral_post_id  UUID REFERENCES viral_posts(id) ON DELETE SET NULL,
  platform       TEXT NOT NULL,
  hook           TEXT,
  structure      TEXT,
  cta            TEXT,
  emotion        TEXT,
  embedding_id   TEXT,                                       -- Qdrant id
  meta           JSONB DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON viral_patterns (platform, emotion);

-- ---------------------------------------------------------------------------
-- AI Agents
-- ---------------------------------------------------------------------------

CREATE TABLE agent_prompts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  version     INT  NOT NULL,
  template    TEXT NOT NULL,
  schema      JSONB,
  is_default  BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (name, version)
);

CREATE TABLE agent_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id        UUID REFERENCES brands(id) ON DELETE SET NULL,
  agent_name      TEXT NOT NULL,
  prompt_name     TEXT,
  prompt_version  INT,
  model           TEXT,
  provider        TEXT,
  input           JSONB,
  output          JSONB,
  tokens_in       INT,
  tokens_out      INT,
  cost_usd        NUMERIC(10,5),
  latency_ms      INT,
  status          TEXT NOT NULL DEFAULT 'ok',                 -- ok|error|timeout
  error           TEXT,
  parent_run_id   UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON agent_runs (brand_id, agent_name);
CREATE INDEX ON agent_runs (created_at);

-- ---------------------------------------------------------------------------
-- Workflows
-- ---------------------------------------------------------------------------

CREATE TABLE workflows (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id    UUID REFERENCES brands(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  definition  JSONB NOT NULL,                                 -- nodes/edges
  trigger     JSONB NOT NULL,                                 -- {kind:'schedule'|'event'|'webhook', config:{...}}
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workflow_runs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  status      TEXT NOT NULL DEFAULT 'running',
  trigger     JSONB,
  state       JSONB DEFAULT '{}'::jsonb,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  error       TEXT
);
CREATE INDEX ON workflow_runs (workflow_id, started_at);

-- ---------------------------------------------------------------------------
-- Billing & Usage
-- ---------------------------------------------------------------------------

CREATE TABLE plan_limits (
  plan          TEXT PRIMARY KEY,
  max_brands    INT,
  max_workspaces INT,
  monthly_assets INT,
  monthly_video_minutes INT,
  monthly_publish_ops INT,
  monthly_llm_usd NUMERIC(10,2)
);

CREATE TABLE usage_events (
  id           BIGSERIAL PRIMARY KEY,
  account_id   UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id     UUID REFERENCES brands(id),
  kind         TEXT NOT NULL,                                 -- asset_generated|video_minute|publish_op|llm_usd
  amount       NUMERIC(12,4) NOT NULL,
  meta         JSONB DEFAULT '{}'::jsonb,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON usage_events (account_id, occurred_at);

-- ---------------------------------------------------------------------------
-- Audit / Security
-- ---------------------------------------------------------------------------

CREATE TABLE audit_log (
  id          BIGSERIAL PRIMARY KEY,
  account_id  UUID,
  user_id     UUID,
  brand_id    UUID,
  action      TEXT NOT NULL,
  target      TEXT,
  data        JSONB DEFAULT '{}'::jsonb,
  ip          INET,
  ua          TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (account_id, occurred_at);

CREATE TABLE webhooks (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  brand_id    UUID REFERENCES brands(id) ON DELETE CASCADE,
  url         TEXT NOT NULL,
  secret      TEXT NOT NULL,
  events      TEXT[] NOT NULL DEFAULT '{}',
  active      BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Notifications (mobile push, in-app)
-- ---------------------------------------------------------------------------

CREATE TABLE notifications (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id   UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  brand_id     UUID REFERENCES brands(id),
  kind         TEXT NOT NULL,
  title        TEXT NOT NULL,
  body         TEXT,
  data         JSONB DEFAULT '{}'::jsonb,
  read_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON notifications (user_id, read_at);

CREATE TABLE push_tokens (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform     TEXT NOT NULL,                                  -- ios|android
  token        TEXT NOT NULL UNIQUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
