"""Writer agent — generates long-form OR multi-channel social content for an idea.

Dispatch via ctx.inputs['format']:
    'blog'   -> writer.blog prompt
    other    -> writer.social prompt (fans every channel in one call)
"""
from __future__ import annotations

import json

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class WriterAgent(Agent):
    name = "writer"
    default_prompt = "writer.blog"

    async def run(self, ctx: AgentContext) -> AgentResult:
        fmt = ctx.inputs.get("format", "blog")
        idea = ctx.inputs.get("idea", {})
        patterns = ctx.retrieved or []
        notes = ctx.inputs.get("notes", "")
        prompt_name = "writer.blog" if fmt == "blog" else "writer.social"
        p = prompts.get(prompt_name)
        rendered_patterns = "\n".join(
            f"- {x.get('hook', '')} | struct: {x.get('structure', '')} | emo: {x.get('emotion', '')}"
            for x in patterns[:8]
        ) or "(no patterns)"

        if fmt == "blog":
            user = p.template.format(
                brand_name=ctx.brand.get("name"),
                tone=ctx.brand.get("tone", "professional"),
                audience=ctx.brand.get("audience", ""),
                keyword=idea.get("primary_keyword", ""),
                style_guide=json.dumps(ctx.brand.get("style_guide", {})),
                patterns=rendered_patterns,
                notes=notes[:4000],
                angle=idea.get("angle", ""),
                title=idea.get("title", ""),
            )
        else:
            user = p.template.format(
                brand_name=ctx.brand.get("name"),
                tone=ctx.brand.get("tone", "professional"),
                patterns=rendered_patterns,
                idea=json.dumps(idea),
                notes=notes[:3000],
            )
        resp = await llm_router.complete(
            system="You write platform-native content that matches brand voice precisely.",
            user=user,
            json_schema=p.schema,
            max_tokens=8000,
            temperature=0.75,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text, "format": fmt},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
            meta={"format": fmt},
        )
