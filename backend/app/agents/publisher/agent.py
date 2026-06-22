"""Publisher agent — dispatches a content_asset to the correct platform adapter.

This is the planning layer; raw API calls live under app/integrations/<platform>/.
"""
from __future__ import annotations

from app.agents.base import Agent, AgentContext, AgentResult


class PublisherAgent(Agent):
    name = "publisher"

    async def run(self, ctx: AgentContext) -> AgentResult:
        # Pure dispatch logic — no LLM call; the heavy work runs in publishing_tasks.publish_schedule.
        return AgentResult(
            output={
                "dispatched": True,
                "asset_id": str(ctx.inputs.get("asset_id")),
                "channel_id": str(ctx.inputs.get("channel_id")),
                "scheduled_at": ctx.inputs.get("scheduled_at"),
            },
            meta={"agent": self.name},
        )
