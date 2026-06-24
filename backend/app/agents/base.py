"""Agent base — strict typed contract every concrete agent inherits.

Run lifecycle:
  1. ctx prepared by caller (brand, retrieval, prior step output).
  2. .run(ctx) is invoked.
  3. agent uses self.llm to call any provider; logs are persisted by the caller.
  4. .run() returns AgentResult(output: dict, meta: dict).
Errors raise — caller marks the agent_run row 'error'.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID


@dataclass
class AgentContext:
    account_id: UUID
    brand_id: UUID
    brand: dict                          # serialized brand memory (name, tone, audience, ...)
    inputs: dict = field(default_factory=dict)
    retrieved: list[dict] = field(default_factory=list)   # RAG results
    prior: dict = field(default_factory=dict)             # output of upstream agent
    options: dict = field(default_factory=dict)
    parent_run_id: Optional[UUID] = None


@dataclass
class AgentResult:
    output: dict
    meta: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: Optional[str] = None
    provider: Optional[str] = None


class Agent(ABC):
    name: str = "base"
    default_prompt: str | None = None
    default_provider: str = "anthropic"
    default_model: str | None = None

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentResult: ...
