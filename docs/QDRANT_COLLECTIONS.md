# Qdrant Collections

| Collection                       | Vector Size | Distance | Payload Indexes                | Notes |
|----------------------------------|-------------|----------|--------------------------------|-------|
| `brand_{brand_id}_sources`       | 1024 (Voyage 3) | Cosine | `source_id`, `kind`            | Source-material chunks for brand-grounded RAG |
| `brand_{brand_id}_assets`        | 1024        | Cosine | `format`, `status`             | Dedupe & semantic search over past assets |
| `viral_patterns`                 | 1024        | Cosine | `platform`, `emotion`, `format`| Global library used by writer + video agents |
| `viral_posts`                    | 1024        | Cosine | `platform`                     | Raw embeddings for near-dup detection during ingest |
| `competitor_content`             | 1024        | Cosine | `brand_id`, `competitor_id`    | Per-brand competitor mirror for gap analysis |
| `agent_memory_{brand_id}`        | 1024        | Cosine | `agent`, `kind`                | Long-term agent memory (decisions, lessons) |

Naming: `brand_<uuid_hex>_*` so a brand deletion cascades to its Qdrant collections.

Hot path retrieval params:
- writer / strategist: `top_k=12`, `score_threshold=0.30`, `mmr=true`
- video / designer:    `top_k=8`,  `score_threshold=0.35`
- dedup check:         `top_k=1`,  `score_threshold=0.92` (near-duplicate)
