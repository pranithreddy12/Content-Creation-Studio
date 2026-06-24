"""On-demand agent invocation + chat assistant."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.llm_router import llm_router
from app.agents.registry import AGENTS, get_agent
from app.agents.runner import run_agent
from app.api.deps import CurrentUser, DBSession, current_user
from app.models.brand import Brand
from app.services.billing import BudgetExceeded, check_rate
from app.services.provisioning import get_or_create_account

router = APIRouter()


class AgentInvocation(BaseModel):
    agent: str
    brand_id: UUID
    inputs: dict = {}
    options: dict = {}


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant" | "system"
    content: str


class ChatRequest(BaseModel):
    history: list[ChatMessage]
    brand_id: UUID | None = None


@router.post("/invoke")
async def invoke_agent(
    payload: AgentInvocation,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    acct = await get_or_create_account(db, user)
    await check_rate(acct.id, plan=acct.plan)
    brand = (await db.execute(
        select(Brand).where(Brand.id == payload.brand_id, Brand.account_id == acct.id)
    )).scalar_one_or_none()
    if not brand:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    try:
        agent = get_agent(payload.agent)
    except KeyError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown agent: {payload.agent}")
    ctx = AgentContext(
        account_id=acct.id,
        brand_id=brand.id,
        brand={
            "name": brand.name, "tone": brand.tone, "audience": brand.audience,
            "primary_topic": brand.primary_topic, "style_guide": brand.style_guide,
            "messaging": brand.messaging, "daily_quota": brand.daily_quota,
        },
        inputs=payload.inputs,
        options={**payload.options, "db": db},
    )
    result = await run_agent(db, agent, ctx)
    return {
        "agent": payload.agent, "output": result.output,
        "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd, "model": result.model, "provider": result.provider,
    }


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    db: DBSession,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    acct = await get_or_create_account(db, user)
    await check_rate(acct.id, plan=acct.plan)
    msgs = [m for m in payload.history if m.role in ("user", "assistant")]
    if not msgs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty history")
    last_user = next((m for m in reversed(msgs) if m.role == "user"), None)
    if not last_user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no user message")
    convo = "\n".join(f"{m.role.upper()}: {m.content}" for m in msgs[-12:])
    try:
        resp = await llm_router.complete(
            system=("You are the Studio assistant. Help the user plan, rewrite, or analyze content. "
                    "Be concise; use bullet points when listing."),
            user=convo,
            max_tokens=1500,
            temperature=0.5,
            account_id=acct.id,
        )
        return {"reply": resp.text, "model": resp.model, "provider": resp.provider}
    except BudgetExceeded as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc))
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {exc}")


@router.get("")
async def list_available_agents() -> dict:
    return {"agents": list(AGENTS.keys())}
