from __future__ import annotations

import json

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class SEOAgent(Agent):
    name = "seo"
    default_prompt = "seo.optimize"

    async def run(self, ctx: AgentContext) -> AgentResult:
        asset = ctx.inputs.get("asset", {})
        p = prompts.get(self.default_prompt)
        user = p.template.format(asset_json=json.dumps(asset)[:8000])
        resp = await llm_router.complete(
            system="You are a senior technical SEO. Strict JSON only.",
            user=user,
            json_schema=p.schema,
            temperature=0.2,
            max_tokens=2500,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
