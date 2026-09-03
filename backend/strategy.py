"""Regime-aware, defined-risk options strategy router."""

from __future__ import annotations

import math
from datetime import date

from models import AccountSnapshot, MarketSnapshot, OptionContract, StrategyKind, TradeLeg, TradePlan


class StrategyRouter:
    """Convert market evidence into an executable options structure.

    The router is deterministic on purpose: AI opinions can influence confidence
    and veto a proposal, but they cannot invent option symbols or order legs.
    """

    def route(self, market: MarketSnapshot, account: AccountSnapshot, risk_pct: float) -> TradePlan:
        if not market.contracts:
            return self._no_trade(market, "No option-chain evidence was available.")

        budget = account.equity * (risk_pct / 100)

        if market.trend_score >= 0.35 and market.news_sentiment > -0.45:
            plan = self._vertical(market, bullish=True)
        elif market.trend_score <= -0.35 and market.news_sentiment < 0.45:
            plan = self._vertical(market, bullish=False)
        elif abs(market.trend_score) <= 0.20 and market.iv_rank >= 58 and abs(market.news_sentiment) < 0.50:
            plan = self._iron_condor(market)
        elif market.iv_rank <= 25 and market.catalyst_score >= 0.70:
            plan = self._long_straddle(market)
            if plan.strategy != StrategyKind.NO_TRADE and plan.max_loss > budget:
                plan = self._long_strangle(market)
        else:
            return self._no_trade(
                market,
                "The regime and news evidence do not offer enough aligned edge to justify premium risk.",
            )

        if plan.strategy == StrategyKind.NO_TRADE or plan.max_loss <= 0:
            return plan

        affordable_quantity = math.floor(budget / plan.max_loss)
        if affordable_quantity < 1:
            return self._no_trade(
                market,
                (
                    f"The cheapest eligible {plan.strategy.value.replace('_', ' ')} risks "
                    f"${plan.max_loss:,.0f}, above the ${budget:,.0f} per-trade budget."
                ),
            )
        plan.quantity = min(3, affordable_quantity)
        plan.max_loss = round(plan.max_loss * plan.quantity, 2)
        if plan.max_profit is not None:
            plan.max_profit = round(plan.max_profit * plan.quantity, 2)
        plan.thesis += (
            f" News sentiment is {market.news_sentiment:+.2f}. Sized to {plan.quantity} "
            f"structure(s) against a {risk_pct:.2f}% risk budget."
        )
        return plan

    def research_candidate(self, market: MarketSnapshot) -> TradePlan:
        """Build one defined-risk challenger when live evidence says hold cash.

        Research candidates are never returned by the execution router. The lab
        uses them only to compare payoff geometry and stress assumptions across
        every configured watchlist symbol.
        """

        builders = []
        if market.trend_score >= 0.10:
            builders.append(lambda: self._vertical(market, bullish=True))
        elif market.trend_score <= -0.10:
            builders.append(lambda: self._vertical(market, bullish=False))
        if market.iv_rank >= 55:
            builders.append(lambda: self._iron_condor(market))
        if market.catalyst_score >= 0.35 or market.iv_rank <= 35:
            builders.append(lambda: self._long_strangle(market))
        builders.extend(
            [
                lambda: self._vertical(market, bullish=market.trend_score >= 0),
                lambda: self._long_strangle(market),
                lambda: self._iron_condor(market),
            ]
        )

        for build in builders:
            plan = build()
            if plan.strategy != StrategyKind.NO_TRADE and plan.legs and plan.max_loss > 0:
                plan.quantity = 1
                plan.thesis = (
                    "Research challenger only — current execution evidence still says hold cash. "
                    + plan.thesis
                )
                return plan
        return self._no_trade(market, "No complete defined-risk structure could be formed for research.")

    def _eligible(self, market: MarketSnapshot, option_type: str) -> list[OptionContract]:
        return [
            contract
            for contract in market.contracts
            if contract.option_type == option_type and contract.ask > 0 and contract.bid >= 0
        ]

    @staticmethod
    def _closest(contracts: list[OptionContract], target_delta: float) -> OptionContract | None:
        if not contracts:
            return None
        return min(contracts, key=lambda contract: abs(contract.delta - target_delta))

    @staticmethod
    def _leg(contract: OptionContract, side: str) -> TradeLeg:
        return TradeLeg(
            symbol=contract.symbol,
            option_type=contract.option_type,
            side=side,  # type: ignore[arg-type]
            position_intent="buy_to_open" if side == "buy" else "sell_to_open",
            strike=contract.strike,
            expiration=contract.expiration,
            delta=contract.delta,
            mid=contract.mid,
            spread_pct=contract.spread_pct,
            open_interest=contract.open_interest,
        )

    @staticmethod
    def _dte(contracts: list[OptionContract]) -> int:
        if not contracts:
            return 0
        return max(0, (date.fromisoformat(contracts[0].expiration) - date.today()).days)

    def _vertical(self, market: MarketSnapshot, bullish: bool) -> TradePlan:
        option_type = "call" if bullish else "put"
        contracts = self._eligible(market, option_type)
        long_target = 0.55 if bullish else -0.55
        short_target = 0.25 if bullish else -0.25
        long_contract = self._closest(contracts, long_target)
        short_contract = self._closest(contracts, short_target)
        if not long_contract or not short_contract or long_contract.symbol == short_contract.symbol:
            return self._no_trade(market, "The chain could not form a liquid vertical spread.")

        width = abs(short_contract.strike - long_contract.strike)
        debit = max(0.01, long_contract.mid - short_contract.mid)
        strategy = StrategyKind.BULL_CALL_SPREAD if bullish else StrategyKind.BEAR_PUT_SPREAD
        direction = "bullish" if bullish else "bearish"
        return TradePlan(
            symbol=market.symbol,
            strategy=strategy,
            thesis=(
                f"{market.symbol} is in a {market.regime} regime with trend score "
                f"{market.trend_score:+.2f}; a defined-risk {direction} vertical expresses the view "
                "without naked option exposure."
            ),
            confidence=min(0.92, 0.56 + abs(market.trend_score) * 0.42),
            limit_price=round(debit, 2),
            legs=[self._leg(long_contract, "buy"), self._leg(short_contract, "sell")],
            max_loss=round(debit * 100, 2),
            max_profit=round(max(0, width - debit) * 100, 2),
            net_delta=round(long_contract.delta - short_contract.delta, 3),
            net_theta=round(long_contract.theta - short_contract.theta, 3),
            dte=self._dte([long_contract]),
            alternatives_rejected=["naked option: undefined/less controlled exposure", "underlying shares: weaker convexity"],
        )

    def _iron_condor(self, market: MarketSnapshot) -> TradePlan:
        calls = self._eligible(market, "call")
        puts = self._eligible(market, "put")
        short_call = self._closest(calls, 0.22)
        long_call = self._closest(calls, 0.11)
        short_put = self._closest(puts, -0.22)
        long_put = self._closest(puts, -0.11)
        selected = [short_call, long_call, short_put, long_put]
        if any(contract is None for contract in selected) or len({c.symbol for c in selected if c}) < 4:
            return self._no_trade(market, "The chain could not form four distinct liquid condor legs.")
        assert short_call and long_call and short_put and long_put
        credit = (short_call.mid + short_put.mid) - (long_call.mid + long_put.mid)
        call_width = abs(long_call.strike - short_call.strike)
        put_width = abs(short_put.strike - long_put.strike)
        max_loss = max(call_width, put_width) - credit
        return TradePlan(
            symbol=market.symbol,
            strategy=StrategyKind.IRON_CONDOR,
            thesis=(
                f"{market.symbol} has muted direction ({market.trend_score:+.2f}) while IV rank is "
                f"{market.iv_rank:.0f}; a defined-risk condor sells rich premium outside the expected range."
            ),
            confidence=min(0.88, 0.54 + market.iv_rank / 240),
            limit_price=round(max(0.01, credit), 2),
            legs=[
                self._leg(long_put, "buy"),
                self._leg(short_put, "sell"),
                self._leg(short_call, "sell"),
                self._leg(long_call, "buy"),
            ],
            max_loss=round(max(0.01, max_loss) * 100, 2),
            max_profit=round(max(0.01, credit) * 100, 2),
            net_delta=round(sum(leg.delta * (-1 if leg.side == "sell" else 1) for leg in [
                self._leg(long_put, "buy"), self._leg(short_put, "sell"),
                self._leg(short_call, "sell"), self._leg(long_call, "buy")
            ]), 3),
            net_theta=0.21,
            dte=self._dte([short_call]),
            alternatives_rejected=["short strangle: undefined tail risk", "directional spread: insufficient trend evidence"],
        )

    def _long_straddle(self, market: MarketSnapshot) -> TradePlan:
        call = self._closest(self._eligible(market, "call"), 0.50)
        put = self._closest(self._eligible(market, "put"), -0.50)
        if not call or not put:
            return self._no_trade(market, "The chain could not form a liquid at-the-money straddle.")
        debit = call.mid + put.mid
        return TradePlan(
            symbol=market.symbol,
            strategy=StrategyKind.LONG_STRADDLE,
            thesis=(
                f"{market.symbol} shows compressed IV rank ({market.iv_rank:.0f}) ahead of a high catalyst score "
                f"({market.catalyst_score:.2f}); long convexity is preferred to guessing direction."
            ),
            confidence=min(0.86, 0.52 + market.catalyst_score * 0.34),
            limit_price=round(debit, 2),
            legs=[self._leg(call, "buy"), self._leg(put, "buy")],
            max_loss=round(debit * 100, 2),
            max_profit=None,
            net_delta=round(call.delta + put.delta, 3),
            net_theta=round(call.theta + put.theta, 3),
            dte=self._dte([call]),
            alternatives_rejected=["single-leg option: unjustified direction", "short volatility: catalyst gap risk"],
        )

    def _long_strangle(self, market: MarketSnapshot) -> TradePlan:
        call = self._closest(self._eligible(market, "call"), 0.35)
        put = self._closest(self._eligible(market, "put"), -0.35)
        if not call or not put or call.strike == put.strike:
            return self._no_trade(market, "The chain could not form a lower-cost liquid long strangle.")
        debit = call.mid + put.mid
        return TradePlan(
            symbol=market.symbol,
            strategy=StrategyKind.LONG_STRANGLE,
            thesis=(
                f"{market.symbol} has compressed IV rank ({market.iv_rank:.0f}) and a high catalyst score "
                f"({market.catalyst_score:.2f}); an out-of-the-money strangle retains two-sided convexity "
                "while keeping one-contract risk inside the capital budget."
            ),
            confidence=min(0.82, 0.49 + market.catalyst_score * 0.32),
            limit_price=round(max(0.01, debit), 2),
            legs=[self._leg(call, "buy"), self._leg(put, "buy")],
            max_loss=round(max(0.01, debit) * 100, 2),
            max_profit=None,
            net_delta=round(call.delta + put.delta, 3),
            net_theta=round(call.theta + put.theta, 3),
            dte=self._dte([call]),
            alternatives_rejected=[
                "at-the-money straddle: exceeded the per-trade risk budget",
                "single-leg option: unjustified direction",
                "short volatility: catalyst gap risk",
            ],
        )

    @staticmethod
    def _no_trade(market: MarketSnapshot, reason: str) -> TradePlan:
        return TradePlan(
            symbol=market.symbol,
            strategy=StrategyKind.NO_TRADE,
            thesis=f"Capital preserved: {reason}",
            confidence=0.72,
            quantity=0,
            no_trade_reason=reason,
            alternatives_rejected=["forced trade: evidence threshold not met"],
        )
