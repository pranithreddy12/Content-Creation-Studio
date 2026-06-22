from __future__ import annotations

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class DesignerAgent(Agent):
    name = "designer"
    default_prompt = "designer.image_prompts"

    async def run(self, ctx: AgentContext) -> AgentResult:
        p = prompts.get(self.default_prompt)
        user = p.template.format(
            asset_title=ctx.inputs.get("title", ""),
            brand_name=ctx.brand.get("name"),
            tone=ctx.brand.get("tone", "modern"),
        )
        resp = await llm_router.complete(
            system="You write visual prompts an image model can render directly.",
            user=user,
            json_schema=p.schema,
            temperature=0.6,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
