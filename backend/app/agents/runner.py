"""Run + persist an agent invocation; emits an `agent_runs` row, returns AgentResult."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent, AgentContext, AgentResult
from app.core.logging import log
from app.models.agent import AgentRun


async def run_agent(db: AsyncSession, agent: Agent, ctx: AgentContext) -> AgentResult:
    started = time.perf_counter()
    row = AgentRun(
        account_id=ctx.account_id,
        brand_id=ctx.brand_id,
        agent_name=agent.name,
        prompt_name=agent.default_prompt,
        input=ctx.inputs,
        parent_run_id=ctx.parent_run_id,
        created_at=datetime.now(timezone.utc),
        status="ok",
    )
    db.add(row)
    await db.flush()
    try:
        result = await agent.run(ctx)
        row.output = result.output
        row.tokens_in = result.tokens_in
        row.tokens_out = result.tokens_out
        row.cost_usd = result.cost_usd
        row.model = result.model
        row.provider = result.provider
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        row.status = "ok"
        await db.commit()
        log.info(
            "agent_run", agent=agent.name, model=result.model, cost=result.cost_usd,
            tin=result.tokens_in, tout=result.tokens_out, ms=row.latency_ms,
        )
        return result
    except Exception as exc:
        row.status = "error"
        row.error = str(exc)[:2000]
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        await db.commit()
        log.exception("agent_error", agent=agent.name)
        raise
