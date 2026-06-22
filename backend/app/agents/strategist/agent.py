"""Strategist agent — turns opportunities into a content calendar window."""
from __future__ import annotations

import json

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class StrategistAgent(Agent):
    name = "strategist"
    default_prompt = "strategist.calendar"

    async def run(self, ctx: AgentContext) -> AgentResult:
        window = ctx.inputs.get("window", "weekly")
        themes = ctx.inputs.get("themes") or ctx.prior.get("trending", [])
        p = prompts.get(self.default_prompt)
        user = p.template.format(
            window=window,
            brand_name=ctx.brand.get("name"),
            daily_quota=ctx.brand.get("daily_quota", 1),
            audience=ctx.brand.get("audience", ""),
            themes="\n".join(f"- {t}" for t in themes[:30]) or "(none)",
        )
        resp = await llm_router.complete(
            system="You plan content calendars. Strict JSON only.",
            user=user,
            json_schema=p.schema,
            temperature=0.5,
            max_tokens=6000,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
