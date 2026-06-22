"""Learning agent — converts analytics insights into pattern_score updates (EMA)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.llm_router import llm_router
from app.agents.prompt_registry import prompts
from app.models.analytics import PatternScore

ALPHA = 0.25  # EMA learning rate


class LearningAgent(Agent):
    name = "learning"
    default_prompt = "learning.update_patterns"

    async def run(self, ctx: AgentContext) -> AgentResult:
        summary = ctx.inputs.get("summary", {})
        db: AsyncSession | None = ctx.options.get("db")
        p = prompts.get(self.default_prompt)
        user = p.template.format(summary=json.dumps(summary)[:6000])
        resp = await llm_router.complete(
            system="You update prior beliefs about which content patterns work. Strict JSON.",
            user=user,
            json_schema=p.schema,
            temperature=0.2,
        )
        updates = (resp.json_out or {}).get("updates", [])
        applied = 0
        if db is not None:
            for u in updates:
                key, val, delta = u.get("pattern_key"), u.get("pattern_val"), float(u.get("delta", 0))
                if not (key and val):
                    continue
                row = (await db.execute(
                    select(PatternScore).where(
                        PatternScore.brand_id == ctx.brand_id,
                        PatternScore.pattern_key == key,
                        PatternScore.pattern_val == val,
                    )
                )).scalar_one_or_none()
                if row:
                    row.ema_score = float(row.ema_score) * (1 - ALPHA) + delta * ALPHA
                    row.sample_n += 1
                    row.updated_at = datetime.now(timezone.utc)
                else:
                    db.add(PatternScore(
                        brand_id=ctx.brand_id,
                        pattern_key=key,
                        pattern_val=val,
                        ema_score=delta,
                        sample_n=1,
                        updated_at=datetime.now(timezone.utc),
                    ))
                applied += 1
            await db.commit()
        return AgentResult(
            output={"updates": updates, "applied": applied},
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cost_usd=resp.cost_usd,
            model=resp.model,
            provider=resp.provider,
        )
