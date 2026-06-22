"""Versioned in-memory prompt registry. Persisted to `agent_prompts` for audit + A/B."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Prompt:
    name: str
    version: int
    template: str
    schema: dict | None = None


class PromptRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Prompt] = {}

    def register(self, p: Prompt) -> None:
        key = f"{p.name}:v{p.version}"
        self._by_name[key] = p
        cur = self._by_name.get(p.name)
        if cur is None or cur.version <= p.version:
            self._by_name[p.name] = p

    def get(self, name: str, version: int | None = None) -> Prompt:
        key = f"{name}:v{version}" if version else name
        if key not in self._by_name:
            raise KeyError(f"prompt {key} not registered")
        return self._by_name[key]

    def render(self, name: str, **kwargs: Any) -> str:
        return self.get(name).template.format(**kwargs)


prompts = PromptRegistry()


# -------- canonical prompts --------

prompts.register(Prompt(
    name="research.synthesize",
    version=1,
    template=(
        "You are a senior content strategist analyzing the latest industry signal for {brand_name}.\n"
        "Brand topic: {primary_topic}\nAudience: {audience}\n"
        "Below are recent items (news, reddit, quora, x, youtube, competitor moves).\n"
        "Extract: (a) popular questions, (b) trending topics, (c) viral formats spotted, "
        "(d) emerging keywords. Be specific and concrete.\n\n"
        "ITEMS:\n{items}"
    ),
    schema={
        "type": "object",
        "properties": {
            "questions":     {"type": "array", "items": {"type": "string"}},
            "trending":      {"type": "array", "items": {"type": "string"}},
            "viral_formats": {"type": "array", "items": {"type": "string"}},
            "keywords":      {"type": "array", "items": {"type": "string"}},
        },
        "required": ["questions", "trending", "viral_formats", "keywords"],
    },
))

prompts.register(Prompt(
    name="ideas.generate",
    version=1,
    template=(
        "Generate {n} distinct content ideas for {brand_name}.\n"
        "Brand tone: {tone}. Audience: {audience}.\n"
        "Lean on these opportunities:\n{opportunities}\n"
        "Each idea must include a compelling working title, the angle, target audience, "
        "primary keyword, secondary keywords (3-5), and 2-4 best formats."
    ),
    schema={
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title":             {"type": "string"},
                        "angle":             {"type": "string"},
                        "audience":          {"type": "string"},
                        "primary_keyword":   {"type": "string"},
                        "secondary_keywords":{"type": "array", "items": {"type": "string"}},
                        "formats":           {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "angle", "primary_keyword", "formats"],
                },
            }
        },
        "required": ["ideas"],
    },
))

prompts.register(Prompt(
    name="ideas.score",
    version=1,
    template=(
        "Score this content idea on 4 dimensions, 0.000-1.000:\n"
        "search_volume, trend_velocity, competition (lower=better), engagement_est.\n"
        "Also compute composite_score = 0.30*search_volume + 0.25*trend_velocity + "
        "0.20*(1-competition) + 0.25*engagement_est.\n\n"
        "Brand: {brand_name}\nIdea: {idea}"
    ),
    schema={
        "type": "object",
        "properties": {
            "search_volume":   {"type": "number"},
            "trend_velocity":  {"type": "number"},
            "competition":     {"type": "number"},
            "engagement_est":  {"type": "number"},
            "composite_score": {"type": "number"},
            "reason":          {"type": "string"},
        },
        "required": ["composite_score"],
    },
))

prompts.register(Prompt(
    name="writer.blog",
    version=1,
    template=(
        "Write a long-form blog article (1500-2200 words) on the angle below for {brand_name}.\n"
        "Brand tone: {tone}. Audience: {audience}. Primary keyword: {keyword}.\n"
        "Voice rules: {style_guide}.\n\n"
        "Use these viral hook patterns where appropriate (do NOT cite them):\n{patterns}\n\n"
        "Source notes:\n{notes}\n\n"
        "ANGLE: {angle}\nTITLE: {title}\n\n"
        "Return JSON with: title (SEO + curiosity), slug, meta_description (150-160 chars), "
        "outline (h2/h3 nesting), body_markdown (full article, with H2/H3 and 1-2 internal-link placeholders [[LINK:topic]])."
    ),
    schema={
        "type": "object",
        "properties": {
            "title":            {"type": "string"},
            "slug":             {"type": "string"},
            "meta_description": {"type": "string"},
            "outline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "h2":  {"type": "string"},
                        "h3":  {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "body_markdown":    {"type": "string"},
        },
        "required": ["title", "body_markdown"],
    },
))

prompts.register(Prompt(
    name="writer.social",
    version=1,
    template=(
        "Generate platform-native variants of the following idea for {brand_name}.\n"
        "Tone: {tone}. Patterns to use:\n{patterns}\n\n"
        "Idea: {idea}\nSource notes: {notes}\n\n"
        "Return JSON with these keys: linkedin (1 post 1200-1800 chars + 3-5 hashtags), "
        "x_thread (array of 6-10 tweets ≤280 chars each), instagram (caption + 5-10 hashtags), "
        "carousel (array of 7-10 slide objects {{headline, body}}), reel_script "
        "(array of 6-10 beats {{type:'hook|line|cta', text, on_screen}}), short_script (same shape, 30-45s), "
        "tiktok_script (same shape, 30-60s), email_newsletter (subject + body_markdown), "
        "sales_email (subject + body_markdown), landing_copy (hero_headline, sub, bullets[], cta), "
        "ad_copy ({{primary_text, headline, description}}), facebook_post, reddit_post (title + body), "
        "quora_answer (markdown), youtube_script (array of beats)."
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="seo.optimize",
    version=1,
    template=(
        "Audit and optimize SEO for the asset below. Output JSON with: title (≤60c), "
        "meta_description (≤160c), slug, focus_keyword, secondary_keywords[], "
        "internal_link_targets[] (topic phrases), jsonld (BlogPosting schema), readability_score, "
        "recommendations[].\n\nASSET:\n{asset_json}"
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="designer.image_prompts",
    version=1,
    template=(
        "Generate concrete image prompts for asset '{asset_title}' (brand: {brand_name}, tone: {tone}).\n"
        "Return JSON with: hero_image, infographic, thumbnail, social_graphic, "
        "carousel_slides[] (one prompt per slide). Each prompt must include subject, style, lighting, "
        "composition, color palette aligned to brand."
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="video.script",
    version=1,
    template=(
        "Create a {format} video script for: {title}.\n"
        "Brand tone: {tone}. Duration target: {duration}s.\n"
        "Patterns:\n{patterns}\n\n"
        "Return JSON with: hook (≤6 words), beats[] (each {{ts_start, ts_end, narration, on_screen_text, "
        "broll_prompt, sfx}}), cta. Total duration must match target."
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="strategist.calendar",
    version=1,
    template=(
        "Plan a {window} content calendar for {brand_name}.\n"
        "Daily quota: {daily_quota}. Audience: {audience}. Themes:\n{themes}\n\n"
        "Return JSON: days[] (each {{date, theme, ideas[]}}). Balance formats across the window."
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="viral.extract",
    version=1,
    template=(
        "Extract the underlying pattern of this {platform} post.\n"
        "POST:\n{raw}\n\n"
        "Return JSON: hook (short label), structure (e.g. 'problem-agitate-solve'), "
        "cta (style), emotion (one of: curiosity, awe, fear, anger, joy, surprise, relatability)."
    ),
    schema={
        "type": "object",
        "properties": {
            "hook":      {"type": "string"},
            "structure": {"type": "string"},
            "cta":       {"type": "string"},
            "emotion":   {"type": "string"},
        },
        "required": ["hook", "structure", "emotion"],
    },
))

prompts.register(Prompt(
    name="analytics.insights",
    version=1,
    template=(
        "Given the recent performance below, identify the top 5 insights for {brand_name} this week.\n"
        "Group insights by: best_formats, best_hooks, best_topics, best_times.\n\n"
        "DATA:\n{rows}"
    ),
    schema={"type": "object"},
))

prompts.register(Prompt(
    name="learning.update_patterns",
    version=1,
    template=(
        "From the analytics summary below, suggest pattern_score updates. For each pattern, give "
        "pattern_key in {{hook_type, structure, cta_style, emotion, format}}, pattern_val, and a delta "
        "(0.0-1.0) representing how strongly this pattern outperformed peers.\n\nSUMMARY:\n{summary}"
    ),
    schema={
        "type": "object",
        "properties": {
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pattern_key": {"type": "string"},
                        "pattern_val": {"type": "string"},
                        "delta":       {"type": "number"},
                    },
                    "required": ["pattern_key", "pattern_val", "delta"],
                },
            }
        },
        "required": ["updates"],
    },
))
