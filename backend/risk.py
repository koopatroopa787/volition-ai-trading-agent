"""Deterministic portfolio risk constitution.

This module deliberately contains no model calls. Its veto is final.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config import Settings
from models import AccountSnapshot, GateResult, MarketSnapshot, StrategyKind, TradePlan


LEVEL_THREE_STRATEGIES = {
    StrategyKind.BULL_CALL_SPREAD,
    StrategyKind.BEAR_PUT_SPREAD,
    StrategyKind.IRON_CONDOR,
    StrategyKind.LONG_STRADDLE,
    StrategyKind.LONG_STRANGLE,
}


class RiskConstitution:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        plan: TradePlan,
        account: AccountSnapshot,
        market: MarketSnapshot,
        kill_switch: bool,
    ) -> list[GateResult]:
        if plan.strategy == StrategyKind.NO_TRADE:
            return [
                GateResult(
                    name="evidence threshold",
                    passed=True,
                    observed=plan.no_trade_reason or "no qualifying edge",
                    limit="No trade is permitted and recorded",
                    severity="info",
                )
            ]

        risk_pct = (plan.max_loss / account.equity * 100) if account.equity else 100.0
        open_risk_after = ((account.open_risk + plan.max_loss) / account.equity * 100) if account.equity else 100.0
        required_level = 3 if plan.strategy in LEVEL_THREE_STRATEGIES else 2
        liquid = all(
            leg.open_interest >= self.settings.min_open_interest
            and leg.spread_pct <= self.settings.max_bid_ask_spread_pct
            for leg in plan.legs
        )
        quote_age = max(0.0, (datetime.now(timezone.utc) - market.captured_at).total_seconds())
        structure_safe, structure_observed = self._defined_risk_structure(plan)

        return [
            GateResult(
                name="kill switch",
                passed=not kill_switch,
                observed="armed" if kill_switch else "clear",
                limit="must be clear",
            ),
            GateResult(
                name="paper account only",
                passed=self.settings.alpaca_paper_trade,
                observed="paper" if self.settings.alpaca_paper_trade else "live",
                limit="paper",
            ),
            GateResult(
                name="account status",
                passed=account.status.upper() == "ACTIVE",
                observed=account.status,
                limit="ACTIVE",
            ),
            GateResult(
                name="market window",
                passed=market.market_open,
                observed="open" if market.market_open else "closed",
                limit="open for new orders",
            ),
            GateResult(
                name="options permission",
                passed=account.options_trading_level >= required_level,
                observed=f"level {account.options_trading_level}",
                limit=f"level {required_level}+",
            ),
            GateResult(
                name="defined risk",
                passed=structure_safe,
                observed=f"{structure_observed}; max loss ${plan.max_loss:,.0f}",
                limit="complete protective geometry; finite max loss; no naked short legs",
            ),
            GateResult(
                name="risk per trade",
                passed=risk_pct <= self.settings.max_risk_per_trade_pct,
                observed=f"{risk_pct:.2f}%",
                limit=f"≤ {self.settings.max_risk_per_trade_pct:.2f}%",
            ),
            GateResult(
                name="portfolio open risk",
                passed=open_risk_after <= self.settings.max_portfolio_open_risk_pct,
                observed=f"{open_risk_after:.2f}% after trade",
                limit=f"≤ {self.settings.max_portfolio_open_risk_pct:.2f}%",
            ),
            GateResult(
                name="daily loss circuit breaker",
                passed=account.daily_pnl_pct > -self.settings.max_daily_loss_pct,
                observed=f"{account.daily_pnl_pct:+.2f}%",
                limit=f"> -{self.settings.max_daily_loss_pct:.2f}%",
            ),
            GateResult(
                name="position count",
                passed=account.open_positions < self.settings.max_positions,
                observed=str(account.open_positions),
                limit=f"< {self.settings.max_positions}",
            ),
            GateResult(
                name="expiry window",
                passed=self.settings.min_dte <= plan.dte <= self.settings.max_dte,
                observed=f"{plan.dte} DTE",
                limit=f"{self.settings.min_dte}–{self.settings.max_dte} DTE",
            ),
            GateResult(
                name="chain liquidity",
                passed=liquid,
                observed=(
                    f"min OI {min((leg.open_interest for leg in plan.legs), default=0):,}; "
                    f"max spread {max((leg.spread_pct for leg in plan.legs), default=100):.2f}%"
                ),
                limit=(
                    f"OI ≥ {self.settings.min_open_interest:,}; "
                    f"spread ≤ {self.settings.max_bid_ask_spread_pct:.2f}%"
                ),
            ),
            GateResult(
                name="quote freshness",
                passed=quote_age <= self.settings.max_quote_age_seconds,
                observed=f"snapshot age {quote_age:.0f}s",
                limit=f"≤ {self.settings.max_quote_age_seconds}s",
            ),
        ]

    @staticmethod
    def _defined_risk_structure(plan: TradePlan) -> tuple[bool, str]:
        if plan.max_loss <= 0 or plan.quantity < 1 or not plan.legs:
            return False, "missing finite-risk structure"
        expirations = {leg.expiration for leg in plan.legs}
        if len(expirations) != 1 or any(leg.ratio_qty < 1 for leg in plan.legs):
            return False, "legs do not share one valid expiry and ratio"

        buys = [leg for leg in plan.legs if leg.side == "buy"]
        sells = [leg for leg in plan.legs if leg.side == "sell"]
        if plan.strategy in {
            StrategyKind.LONG_CALL,
            StrategyKind.LONG_PUT,
            StrategyKind.LONG_STRADDLE,
            StrategyKind.LONG_STRANGLE,
        }:
            return (len(buys) == len(plan.legs), "all premium is purchased")

        if plan.strategy == StrategyKind.BULL_CALL_SPREAD:
            calls = [leg for leg in plan.legs if leg.option_type == "call"]
            safe = (
                len(calls) == 2
                and len(buys) == len(sells) == 1
                and buys[0].option_type == sells[0].option_type == "call"
                and buys[0].strike < sells[0].strike
                and buys[0].ratio_qty == sells[0].ratio_qty
            )
            return safe, "lower call owned against higher short call" if safe else "invalid call-spread geometry"

        if plan.strategy == StrategyKind.BEAR_PUT_SPREAD:
            puts = [leg for leg in plan.legs if leg.option_type == "put"]
            safe = (
                len(puts) == 2
                and len(buys) == len(sells) == 1
                and buys[0].option_type == sells[0].option_type == "put"
                and buys[0].strike > sells[0].strike
                and buys[0].ratio_qty == sells[0].ratio_qty
            )
            return safe, "higher put owned against lower short put" if safe else "invalid put-spread geometry"

        if plan.strategy == StrategyKind.IRON_CONDOR:
            long_puts = [leg for leg in buys if leg.option_type == "put"]
            short_puts = [leg for leg in sells if leg.option_type == "put"]
            long_calls = [leg for leg in buys if leg.option_type == "call"]
            short_calls = [leg for leg in sells if leg.option_type == "call"]
            safe = (
                all(len(rows) == 1 for rows in (long_puts, short_puts, long_calls, short_calls))
                and long_puts[0].strike < short_puts[0].strike
                and short_calls[0].strike < long_calls[0].strike
                and len({leg.ratio_qty for leg in plan.legs}) == 1
            )
            return safe, "both short wings have farther-out protection" if safe else "invalid condor wing geometry"
        return False, "strategy geometry is not approved"

    @staticmethod
    def approved(gates: list[GateResult]) -> bool:
        return all(gate.passed for gate in gates if gate.severity == "block")
