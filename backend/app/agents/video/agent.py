from __future__ import annotations

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts


class VideoAgent(Agent):
    name = "video"
    default_prompt = "video.script"

    async def run(self, ctx: AgentContext) -> AgentResult:
        fmt = ctx.inputs.get("format", "reel")  # reel|short|tiktok|yt_long
        duration = ctx.inputs.get("duration", 30 if fmt != "yt_long" else 480)
        patterns = ctx.retrieved or []
        rendered = "\n".join(
            f"- hook: {x.get('hook')} | structure: {x.get('structure')}" for x in patterns[:5]
        ) or "(no patterns)"
        p = prompts.get(self.default_prompt)
        user = p.template.format(
            format=fmt,
            title=ctx.inputs.get("title", ""),
            tone=ctx.brand.get("tone", "energetic"),
            duration=duration,
            patterns=rendered,
        )
        resp = await llm_router.complete(
            system="You design short-form video scripts. Every beat must be filmable.",
            user=user,
            json_schema=p.schema,
            temperature=0.8,
            max_tokens=4000,
        )
        return AgentResult(
            output=resp.json_out or {"raw": resp.text},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
            meta={"format": fmt, "duration": duration},
        )
