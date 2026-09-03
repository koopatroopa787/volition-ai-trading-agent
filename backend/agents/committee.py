"""Independent AI opinions over deterministic trade structures."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from config import Settings
from models import AgentOpinion, MarketSnapshot, StrategyKind, TradePlan


@dataclass(frozen=True)
class Role:
    name: str
    mandate: str


ROLES = (
    Role("Regime Sentinel", "Challenge whether trend and market regime support the proposed direction."),
    Role("Volatility Architect", "Judge implied-versus-realized volatility, convexity, and structure choice."),
    Role("Adversarial Skeptic", "Find the strongest reason to preserve cash; oppose weak or fragile evidence."),
)


class ReasoningCommittee:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def deliberate(self, market: MarketSnapshot, plan: TradePlan) -> list[AgentOpinion]:
        if plan.strategy == StrategyKind.NO_TRADE:
            return [self._fallback(role, market, plan) for role in ROLES]
        if not self.settings.reasoning_enabled:
            return [self._fallback(role, market, plan) for role in ROLES]
        return await asyncio.gather(*(self._ask(role, market, plan) for role in ROLES))

    async def _ask(self, role: Role, market: MarketSnapshot, plan: TradePlan) -> AgentOpinion:
        evidence = {
            "symbol": market.symbol,
            "regime": market.regime,
            "trend_score": market.trend_score,
            "realized_volatility": market.realized_volatility,
            "iv_score": market.iv_rank,
            "catalyst_score": market.catalyst_score,
            "strategy": plan.strategy,
            "max_loss": plan.max_loss,
            "max_profit": plan.max_profit,
            "net_delta": plan.net_delta,
            "dte": plan.dte,
            "legs": [leg.model_dump() for leg in plan.legs],
        }
        system = (
            f"You are the {role.name} on an autonomous options risk committee. {role.mandate} "
            "You cannot change order legs or bypass deterministic gates. Respond only as JSON with keys: "
            "verdict (support|oppose|abstain), confidence (0..1), summary (one sentence), evidence (2 short strings). "
            "Prefer no trade when evidence is ambiguous. This is paper trading, not investment advice."
        )
        payload = {
            "model": self.settings.effective_reasoning_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(evidence, separators=(",", ":"))},
            ],
            "temperature": 0.15,
            "max_tokens": 260,
        }
        try:
            # CPU-only local models can need roughly a minute for their first
            # response while weights are loaded. Keep the call bounded, but
            # leave enough room for the three independently queued opinions.
            async with httpx.AsyncClient(timeout=150) as client:
                headers = {}
                if self.settings.effective_reasoning_api_key:
                    headers["Authorization"] = f"Bearer {self.settings.effective_reasoning_api_key}"
                response = await client.post(
                    f"{self.settings.effective_reasoning_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            body = json.loads(match.group(0) if match else text)
            return AgentOpinion(
                agent=role.name,
                verdict=body.get("verdict", "abstain"),
                confidence=max(0.0, min(1.0, float(body.get("confidence", 0.5)))),
                summary=str(body.get("summary", "No concise opinion returned."))[:320],
                evidence=[str(item)[:180] for item in body.get("evidence", [])][:3],
                model=(
                    f"{self.settings.effective_reasoning_provider} · "
                    f"{self.settings.effective_reasoning_model}"
                ).strip(" ·"),
            )
        except Exception:  # external reasoning must never take down the risk loop
            return self._fallback(role, market, plan)

    def _fallback(self, role: Role, market: MarketSnapshot, plan: TradePlan) -> AgentOpinion:
        if plan.strategy == StrategyKind.NO_TRADE:
            return AgentOpinion(
                agent=role.name,
                verdict="support",
                confidence=0.78,
                summary="The evidence threshold was not met, so preserving cash is the disciplined outcome.",
                evidence=[plan.no_trade_reason or "No qualifying edge", "No option premium placed at risk"],
            )

        if role.name == "Regime Sentinel":
            alignment = abs(market.trend_score) >= 0.35 or plan.strategy == StrategyKind.IRON_CONDOR
            return AgentOpinion(
                agent=role.name,
                verdict="support" if alignment else "oppose",
                confidence=min(0.92, 0.58 + abs(market.trend_score) * 0.35),
                summary=f"The {market.regime} classification {'supports' if alignment else 'does not clearly support'} the structure.",
                evidence=[f"trend {market.trend_score:+.2f}", f"regime {market.regime}"],
            )
        if role.name == "Volatility Architect":
            aligned = (
                (plan.strategy == StrategyKind.IRON_CONDOR and market.iv_rank >= 58)
                or (plan.strategy == StrategyKind.LONG_STRADDLE and market.iv_rank <= 25)
                or plan.strategy in {StrategyKind.BULL_CALL_SPREAD, StrategyKind.BEAR_PUT_SPREAD}
            )
            return AgentOpinion(
                agent=role.name,
                verdict="support" if aligned else "oppose",
                confidence=0.81 if aligned else 0.67,
                summary="The premium regime is consistent with the selected defined-risk structure." if aligned else "Premium pricing conflicts with the proposed volatility exposure.",
                evidence=[f"IV score {market.iv_rank:.0f}", f"realized vol {market.realized_volatility:.1%}"],
            )
        fragile = plan.confidence < 0.68 or plan.max_loss <= 0
        return AgentOpinion(
            agent=role.name,
            verdict="oppose" if fragile else "support",
            confidence=0.74,
            summary="The proposal has finite loss and explicit invalidation; no stronger veto was found." if not fragile else "The proposal is too fragile to spend the risk budget.",
            evidence=[f"max loss ${plan.max_loss:,.0f}", f"confidence {plan.confidence:.0%}"],
        )

    @staticmethod
    def supports(plan: TradePlan, opinions: list[AgentOpinion]) -> bool:
        if plan.strategy == StrategyKind.NO_TRADE:
            return True
        support = sum(1 for opinion in opinions if opinion.verdict == "support")
        oppose = sum(1 for opinion in opinions if opinion.verdict == "oppose")
        return support >= 2 and oppose <= 1
