"""Multi-provider LLM router with structured-output enforcement + retry fallback.

Providers: Anthropic Claude, OpenAI, Google Gemini.
Always returns: dict(text, json | None, tokens_in, tokens_out, cost_usd, model, provider).
"""
from __future__ import annotations

import contextvars
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import httpx
from anthropic import Anthropic
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import log
from app.services.billing import budget

# Billing context — set at request/Celery-task entry so the LLM chokepoint can
# charge the right tenant. complete() FAILS CLOSED when this is unset: we never
# make an unmetered provider call.
_billing_account: contextvars.ContextVar[Optional[UUID]] = contextvars.ContextVar(
    "llm_billing_account", default=None
)
_billing_brand: contextvars.ContextVar[Optional[UUID]] = contextvars.ContextVar(
    "llm_billing_brand", default=None
)


def set_billing_context(account_id: UUID | None, brand_id: UUID | None = None) -> None:
    _billing_account.set(account_id)
    _billing_brand.set(brand_id)


def _estimate_input_tokens(text: str) -> int:
    # ~4 chars/token is the standard rough heuristic; good enough for a reservation.
    return max(1, len(text) // 4)


# --- pricing table (USD per 1M tokens, input / output) ---
PRICING = {
    # Anthropic — Claude 4.x family
    "claude-opus-4-7":           (15.0, 75.0),
    "claude-opus-4-8":           (15.0, 75.0),
    "claude-sonnet-4-6":         (3.0,  15.0),
    "claude-haiku-4-5-20251001": (1.0,  5.0),
    # OpenAI
    "gpt-4o":                    (2.5,  10.0),
    "gpt-4o-mini":               (0.15, 0.6),
    "o4-mini":                   (3.0,  12.0),
    # Gemini
    "gemini-2.5-pro":            (1.25, 10.0),
    "gemini-2.5-flash":          (0.075, 0.3),
}


@dataclass
class LLMResponse:
    text: str
    json_out: Optional[dict]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    provider: str
    latency_ms: int


def _price(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return (tin / 1_000_000) * pin + (tout / 1_000_000) * pout


class LLMRouter:
    DEFAULT_CHAIN = [
        ("anthropic", "claude-sonnet-4-6"),
        ("openai",    "gpt-4o"),
        ("gemini",    "gemini-2.5-pro"),
    ]

    def __init__(self) -> None:
        self._anthropic = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
        self._openai = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        self._gemini_key = settings.gemini_api_key

    async def complete(
        self,
        *,
        system: str,
        user: str,
        json_schema: Optional[dict] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        account_id: Optional[UUID] = None,
    ) -> LLMResponse:
        """Account-aware chokepoint. Reserves budget BEFORE the (retrying) call and
        reconciles after; fails closed if no billing account is in context."""
        acct = account_id or _billing_account.get()
        if acct is None:
            # Never make an unmetered provider call.
            raise budget.BudgetUnset("no billing account in context for LLM call")

        chain = [(provider, model)] if provider and model else self.DEFAULT_CHAIN
        est_model = chain[0][1]
        # Conservative estimate: counted input tokens + the full max_tokens priced as output.
        estimate_usd = _price(est_model, _estimate_input_tokens(system + user), max_tokens)

        # Reserve OUTSIDE the retry loop so retries don't double-charge.
        reserved = await budget.reserve(acct, estimate_usd)
        try:
            resp = await self._complete_chain(
                system=system, user=user, json_schema=json_schema,
                provider=provider, model=model, max_tokens=max_tokens, temperature=temperature,
            )
        except Exception:
            # Call never produced billable usage — release the whole reservation.
            await budget.reconcile(acct, reserved, 0.0)
            raise
        # Reconcile estimate→actual and write the durable usage event.
        await budget.reconcile(acct, reserved, resp.cost_usd)
        await budget.record_actual(acct, _billing_brand.get(), resp.cost_usd)
        return resp

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception_type(Exception),
    )
    async def _complete_chain(
        self,
        *,
        system: str,
        user: str,
        json_schema: Optional[dict] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        chain = [(provider, model)] if provider and model else self.DEFAULT_CHAIN
        last_err: Exception | None = None
        for prov, mdl in chain:
            try:
                return await self._call(prov, mdl, system, user, json_schema, max_tokens, temperature)
            except Exception as exc:
                last_err = exc
                log.warning("llm_provider_failed", provider=prov, model=mdl, err=str(exc)[:200])
                continue
        if last_err:
            raise last_err
        raise RuntimeError("no llm provider available")

    async def _call(
        self,
        provider: str,
        model: str,
        system: str,
        user: str,
        json_schema: Optional[dict],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        t0 = time.perf_counter()
        if provider == "anthropic":
            if not self._anthropic:
                raise RuntimeError("anthropic not configured")
            sys_full = system
            if json_schema:
                sys_full += (
                    "\n\nRespond ONLY with a single valid JSON object matching this schema, "
                    "no prose, no markdown fence:\n" + json.dumps(json_schema)
                )
            resp = self._anthropic.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=sys_full,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            tin = resp.usage.input_tokens
            tout = resp.usage.output_tokens
        elif provider == "openai":
            if not self._openai:
                raise RuntimeError("openai not configured")
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if json_schema:
                kwargs["response_format"] = {"type": "json_object"}
                kwargs["messages"][0]["content"] += "\nReturn ONLY JSON matching: " + json.dumps(json_schema)
            resp = self._openai.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            tin = resp.usage.prompt_tokens
            tout = resp.usage.completion_tokens
        elif provider == "gemini":
            if not self._gemini_key:
                raise RuntimeError("gemini not configured")
            sys_full = system
            if json_schema:
                sys_full += "\nReturn ONLY JSON matching: " + json.dumps(json_schema)
            payload = {
                "system_instruction": {"parts": [{"text": sys_full}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    **({"responseMimeType": "application/json"} if json_schema else {}),
                },
            }
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={self._gemini_key}"
            )
            async with httpx.AsyncClient(timeout=60) as cx:
                r = await cx.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            usage = data.get("usageMetadata", {})
            tin = usage.get("promptTokenCount", 0)
            tout = usage.get("candidatesTokenCount", 0)
        else:
            raise ValueError(f"unknown provider: {provider}")

        json_out: dict | None = None
        if json_schema:
            try:
                stripped = text.strip()
                if stripped.startswith("```"):
                    stripped = stripped.strip("`").split("\n", 1)[1]
                    if stripped.endswith("```"):
                        stripped = stripped[: -3]
                json_out = json.loads(stripped)
            except Exception:
                json_out = None
        return LLMResponse(
            text=text,
            json_out=json_out,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=_price(model, tin, tout),
            model=model,
            provider=provider,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


llm_router = LLMRouter()
