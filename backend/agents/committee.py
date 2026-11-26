"""Independent AI opinions over deterministic trade structures."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx

from agents.factcheck import facts_from_prompt, review
from audit import AuditTrail
from config import Settings
from models import AccountSnapshot, AgentOpinion, MarketSnapshot, MonteCarloResult, StrategyKind, TradePlan


@dataclass(frozen=True)
class Role:
    name: str
    mandate: str


ROLES = (
    Role("Regime Sentinel", "Judge whether trend and market regime fit the proposed directional exposure."),
    Role("Volatility Architect", "Judge whether the volatility regime and convexity fit the structure."),
    Role(
        "Adversarial Skeptic",
        "Read limit_checks. Oppose only if an entry has breached true; if none is breached, support. Do not compare numbers yourself and never invent a threshold. "
        "Quote the breached limit and its value in the summary sentence. Write evidence as two short plain-text strings, for example \"expected_pnl_dollars > 0 is breached\" and \"value -10.36\"; never copy limit_checks entries into evidence.",
    ),
)


def limit_checks(evidence: dict) -> list[dict[str, object]]:
    """Do the Skeptic's comparisons in code; a small model reads the result instead of comparing numbers."""
    proposal, stress = evidence["proposal"], evidence.get("stress_test")
    checks = [
        ("max_loss_pct_of_equity <= risk_budget_pct_of_equity", proposal["max_loss_pct_of_equity"],
         proposal["max_loss_pct_of_equity"] is None or proposal["max_loss_pct_of_equity"] > proposal["risk_budget_pct_of_equity"]),
        ("router_confidence_0_to_1 >= 0.68", proposal["router_confidence_0_to1"], proposal["router_confidence_0_to1"] < 0.68),
    ]
    if stress:
        floor = stress.get("expected_pnl_floor_dollars", 0.0)
        checks += [
            (f"expected_pnl_dollars > {floor:g}" if floor else "expected_pnl_dollars > 0", stress["expected_pnl_dollars"], stress["expected_pnl_dollars"] <= floor),
            ("probability_profit_pct >= 35", stress["probability_profit_pct"], stress["probability_profit_pct"] < 35),
            ("probability_near_max_loss_pct <= 60", stress["probability_near_max_loss_pct"],
             stress["probability_near_max_loss_pct"] > 60),
        ]
    return [{"limit": limit, "value": value, "breached": breached} for limit, value, breached in checks]


class ReasoningCommittee:
    def __init__(self, settings: Settings, audit: AuditTrail | None = None) -> None:
        self.settings = settings
        self.audit = audit

    async def deliberate(
        self,
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None = None,
        simulation: MonteCarloResult | None = None,
        cycle_id: str | None = None,
    ) -> list[AgentOpinion]:
        if plan.strategy == StrategyKind.NO_TRADE:
            if self.audit:
                self.audit.append("model_skipped", {"reason": "no_trade", "roles": [role.name for role in ROLES]},
                                  cycle_id=cycle_id, symbol=market.symbol)
            return [self._fallback(role, market, plan) for role in ROLES]
        if not self.settings.reasoning_enabled:
            if self.audit:
                self.audit.append("model_skipped", {"reason": "reasoning_disabled", "roles": [role.name for role in ROLES]},
                                  cycle_id=cycle_id, symbol=market.symbol)
            return [self._fallback(role, market, plan) for role in ROLES]
        if self.audit:
            opinions = await asyncio.gather(
                *(self._ask(role, market, plan, account, simulation, cycle_id) for role in ROLES)
            )
        else:
            opinions = await asyncio.gather(
                *(self._ask(role, market, plan, account, simulation) for role in ROLES)
            )
        return self._factchecked(list(opinions), market, plan, account, simulation, cycle_id)

    def _factchecked(
        self,
        opinions: list[AgentOpinion],
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None,
        simulation: MonteCarloResult | None,
        cycle_id: str | None,
    ) -> list[AgentOpinion]:
        """Check each opinion's numbers against the facts the models were given (agents/factcheck.py)."""
        mode = self.settings.factcheck_mode
        if mode == "off":
            return opinions
        facts = facts_from_prompt(self._evidence(ROLES[0], market, plan, account, simulation))
        checked, report = review(opinions, facts, mode)
        if self.audit:
            self.audit.append("opinion_factcheck", {"mode": mode, "opinions": report},
                              cycle_id=cycle_id, symbol=market.symbol)
        return checked

    async def _ask(
        self,
        role: Role,
        market: MarketSnapshot,
        plan: TradePlan,
        account: AccountSnapshot | None,
        simulation: MonteCarloResult | None,
        cycle_id: str | None = None,
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
        if self.audit:
            self.audit.append("model_request", {"role": role.name, "request": payload},
                              cycle_id=cycle_id, symbol=market.symbol)
        started = time.perf_counter()
        try:
            # The three opinions queue behind one another on a CPU-only model, so the last one waits for the
            # other two as well as its own run. Keep the call bounded, but not shorter than that queue.
            async with httpx.AsyncClient(timeout=self.settings.reasoning_timeout_seconds) as client:
                headers = {}
                if self.settings.effective_reasoning_api_key:
                    headers["Authorization"] = f"Bearer {self.settings.effective_reasoning_api_key}"
                response = await client.post(
                    f"{self.settings.effective_reasoning_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                response_body = response.json()
                text = response_body["choices"][0]["message"]["content"]
            if self.audit:
                self.audit.append("model_response", {
                    "role": role.name, "response": response_body, "http_status": response.status_code,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }, cycle_id=cycle_id, symbol=market.symbol)
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
        except Exception as exc:  # external reasoning must never take down the risk loop
            if self.audit:
                self.audit.append("model_fallback", {
                    "role": role.name, "error_type": type(exc).__name__,
                    "http_status": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }, cycle_id=cycle_id, symbol=market.symbol)
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
            floor = self.settings.expected_pnl_floor(plan.strategy.value, plan.symbol, plan.max_loss)
            if floor:                                   # index-ETF condors only; every other prompt is unchanged
                stress_test["expected_pnl_floor_dollars"] = round(floor, 2)
                stress_test["pass_policy"]["expected_pnl_dollars"] = f"> {floor:.2f}"
        evidence = {
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
        if role.name == "Adversarial Skeptic":
            evidence["limit_checks"] = limit_checks(evidence)
        return evidence

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
