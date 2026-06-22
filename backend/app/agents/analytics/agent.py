from __future__ import annotations

import json

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class AnalyticsAgent(Agent):
    name = "analytics"
    default_prompt = "analytics.insights"

    async def run(self, ctx: AgentContext) -> AgentResult:
        rows = ctx.inputs.get("rows", [])
        p = prompts.get(self.default_prompt)
        user = p.template.format(
            brand_name=ctx.brand.get("name"),
            rows=json.dumps(rows)[:8000],
        )
        resp = await llm_router.complete(
            system="You extract actionable insights from raw metrics. Strict JSON.",
            user=user,
            json_schema=p.schema,
            temperature=0.2,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
