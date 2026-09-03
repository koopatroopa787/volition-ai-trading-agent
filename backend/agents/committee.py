"""Independent AI opinions over deterministic trade structures."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from config import Settings
from models import AccountSnapshot, AgentOpinion, MarketSnapshot, MonteCarloResult, StrategyKind, TradePlan


@dataclass(frozen=True)
class Role:
    name: str
    mandate: str


ROLES = (
    Role("Regime Sentinel", "Judge whether trend and market regime fit the proposed directional exposure."),
    Role("Volatility Architect", "Judge whether the volatility regime and convexity fit the structure."),
    Role("Adversarial Skeptic", "Identify a concrete, quantified reason the proposal may be too fragile."),
)


class ReasoningCommittee:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def deliberate(
        self,
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None = None,
        simulation: MonteCarloResult | None = None,
    ) -> list[AgentOpinion]:
        if plan.strategy == StrategyKind.NO_TRADE:
            return [self._fallback(role, market, plan) for role in ROLES]
        if not self.settings.reasoning_enabled:
            return [self._fallback(role, market, plan) for role in ROLES]
        return await asyncio.gather(
            *(self._ask(role, market, plan, account, simulation) for role in ROLES)
        )

    async def _ask(
        self,
        role: Role,
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None,
        simulation: MonteCarloResult | None,
    ) -> AgentOpinion:
        evidence = self._evidence(role, market, plan, account, simulation)
        system = (
            f"You are the {role.name} on an autonomous options risk committee. {role.mandate} "
            "Your verdict is advisory: deterministic code alone controls paper execution. Use only the supplied "
            "facts and their explicit units. IV rank is a 0-to-100 percentile and is not comparable to an "
            "annualized volatility percentage. Dollar risk must be judged relative to equity and the stated "
            "risk budget. Support when the role check passes unless you can name a concrete, role-specific "
            "contradiction in the supplied evidence. Oppose only for that measurable contradiction; abstain "
            "when a concern is outside your mandate. Do not treat ordinary risk already inside a stated limit "
            "as a veto and do not infer missing facts. You cannot change order legs or bypass deterministic "
            "gates. Respond only as JSON with keys: "
            "verdict (support|oppose|abstain), confidence (0..1), summary (one sentence), evidence (2 short strings). "
            "This is paper trading, not investment advice."
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

    def _evidence(
        self,
        role: Role,
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None,
        simulation: MonteCarloResult | None,
    ) -> dict[str, object]:
        """Return model evidence with explicit scales and decision context.

        The previous flat schema mixed an IV percentile with decimal annualized
        volatility and supplied raw dollar loss without account equity. Small
        local models understandably interpreted those values as contradictions.
        """

        equity = account.equity if account and account.equity > 0 else 0.0
        max_loss_pct = (plan.max_loss / equity * 100) if equity else None
        risk_budget_dollars = (
            equity * self.settings.max_risk_per_trade_pct / 100
            if equity
            else None
        )
        reward_to_risk = (
            plan.max_profit / plan.max_loss
            if plan.max_profit is not None and plan.max_loss > 0
            else None
        )
        stress_test: dict[str, object] | None = None
        if simulation is not None:
            stress_test = {
                "expected_pnl_dollars": simulation.expected_pnl,
                "probability_profit_pct": round(simulation.probability_profit * 100, 2),
                "probability_near_max_loss_pct": round(
                    simulation.probability_near_max_loss * 100,
                    2,
                ),
                "pass_policy": {
                    "expected_pnl_dollars": "> 0",
                    "probability_profit_pct": ">= 35",
                    "probability_near_max_loss_pct": "<= 60",
                },
            }
        return {
            "committee_authority": "advisory_only; deterministic risk gates control execution",
            "role": role.name,
            "role_check": self._role_check(role, market, plan, max_loss_pct),
            "market": {
                "symbol": market.symbol,
                "regime": market.regime,
                "trend_score_minus1_to1": round(market.trend_score, 4),
                "realized_volatility_pct_annualized": round(market.realized_volatility * 100, 2),
                "iv_rank_percentile_0_to100": round(market.iv_rank, 2),
                "news_sentiment_minus1_to1": round(market.news_sentiment, 4),
                "catalyst_score_0_to1": round(market.catalyst_score, 4),
                "catalyst_types": market.catalyst_types,
            },
            "proposal": {
                "strategy": plan.strategy.value,
                "thesis": plan.thesis,
                "router_confidence_0_to1": round(plan.confidence, 4),
                "quantity": plan.quantity,
                "max_loss_dollars": plan.max_loss,
                "max_loss_pct_of_equity": (
                    round(max_loss_pct, 3)
                    if max_loss_pct is not None
                    else None
                ),
                "risk_budget_pct_of_equity": self.settings.max_risk_per_trade_pct,
                "risk_budget_dollars": (
                    round(risk_budget_dollars, 2)
                    if risk_budget_dollars is not None
                    else None
                ),
                "max_profit_dollars": plan.max_profit,
                "reward_to_risk": round(reward_to_risk, 3) if reward_to_risk is not None else None,
                "net_delta": plan.net_delta,
                "days_to_expiry": plan.dte,
                "legs": [leg.model_dump(mode="json") for leg in plan.legs],
            },
            "stress_test": stress_test,
        }

    def _role_check(
        self,
        role: Role,
        market: MarketSnapshot,
        plan: TradePlan,
        max_loss_pct: float | None,
    ) -> dict[str, object]:
        if role.name == "Regime Sentinel":
            if plan.strategy == StrategyKind.BULL_CALL_SPREAD:
                passed = market.trend_score >= 0.35
                policy = "bull call spread requires trend score >= +0.35"
            elif plan.strategy == StrategyKind.BEAR_PUT_SPREAD:
                passed = market.trend_score <= -0.35
                policy = "bear put spread requires trend score <= -0.35"
            elif plan.strategy == StrategyKind.IRON_CONDOR:
                passed = abs(market.trend_score) <= 0.20
                policy = "iron condor requires absolute trend score <= 0.20"
            else:
                passed = abs(market.trend_score) < 0.35
                policy = "two-sided long volatility requires no strong directional trend"
            return {"passed": passed, "policy": policy}

        if role.name == "Volatility Architect":
            if plan.strategy == StrategyKind.IRON_CONDOR:
                passed = market.iv_rank >= 58
                policy = "premium-selling condor requires IV rank >= 58"
            elif plan.strategy in {StrategyKind.LONG_STRADDLE, StrategyKind.LONG_STRANGLE}:
                passed = market.iv_rank <= 25 and market.catalyst_score >= 0.70
                policy = "long volatility requires IV rank <= 25 and catalyst score >= 0.70"
            else:
                passed = True
                policy = "directional vertical is selected by trend; volatility is not its entry trigger"
            return {"passed": passed, "policy": policy}

        passed = (
            plan.max_loss > 0
            and plan.confidence >= 0.68
            and max_loss_pct is not None
            and max_loss_pct <= self.settings.max_risk_per_trade_pct
        )
        return {
            "passed": passed,
            "policy": (
                "finite loss, router confidence >= 0.68, and maximum loss within the "
                f"{self.settings.max_risk_per_trade_pct:.2f}% equity budget"
            ),
        }

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
