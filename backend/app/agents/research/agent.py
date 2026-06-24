"""Research agent — given raw research_items, synthesizes structured opportunities."""
from __future__ import annotations

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class ResearchAgent(Agent):
    name = "research"
    default_prompt = "research.synthesize"

    async def run(self, ctx: AgentContext) -> AgentResult:
        items = ctx.inputs.get("items", [])
        rendered = "\n".join(
            f"- [{it.get('channel')}] {it.get('title') or ''} :: {it.get('url') or ''}\n  {it.get('excerpt') or ''}"
            for it in items[:80]
        ) or "(no items)"
        p = prompts.get(self.default_prompt)
        user = p.template.format(
            brand_name=ctx.brand.get("name", "the brand"),
            primary_topic=ctx.brand.get("primary_topic", ""),
            audience=ctx.brand.get("audience", ""),
            items=rendered,
        )
        resp = await llm_router.complete(
            system="You synthesize signal from noise. Strict JSON only.",
            user=user,
            json_schema=p.schema,
            temperature=0.4,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
