export interface Brand {
  id: string;
  account_id: string;
  workspace_id: string;
  name: string;
  slug: string;
  description?: string;
  website_url?: string;
  product_url?: string;
  competitor_urls: string[];
  primary_topic?: string;
  audience?: string;
  tone?: string;
  daily_quota: number;
  timezone: string;
  publish_window: { start: string; end: string };
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Source {
  id: string;
  brand_id: string;
  kind: string;
  title?: string;
  url?: string;
  storage_key?: string;
  status: string;
  error?: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ContentAsset {
  id: string;
  brand_id: string;
  idea_id: string;
  format: string;
  title?: string;
  body?: string;
  body_json?: unknown;
  word_count?: number;
  status: "draft" | "review" | "approved" | "scheduled" | "published" | "failed";
  seo: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Schedule {
  id: string;
  brand_id: string;
  asset_id: string;
  channel_id: string;
  scheduled_at: string;
  status: string;
  external_url?: string;
}

export interface Channel {
  id: string;
  platform: string;
  display_name: string;
  status: string;
  meta: Record<string, unknown>;
}

export interface AssetMetricRow {
  platform: string;
  views: number;
  clicks: number;
  shares: number;
  likes: number;
  comments: number;
  ctr: number;
  collected_at: string;
}
