"""Reproducible expiry stress tests for defined-risk option structures."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Literal

from models import (
    HistogramBin,
    MarketSnapshot,
    MonteCarloResult,
    OutcomeScenario,
    PercentileBandPoint,
    ProbabilityCheckpoint,
    StrategyKind,
    TradePlan,
)


def _percentile(values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile from an already sorted list."""

    if not values:
        return 0.0
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


class MonteCarloSimulator:
    """Simulate underlying paths, then settle the proposed option legs at expiry.

    This deliberately avoids pretending to predict option marks between now and
    expiry. The path fan describes the underlying; the outcome distribution uses
    each leg's exact expiry intrinsic value and the proposal's quoted net premium.
    """

    _CREDIT_STRATEGIES = {StrategyKind.IRON_CONDOR}
    _SCENARIOS = {
        "base": (
            "Current evidence",
            "Uses the strategy desk's current trend, news and volatility evidence.",
            1.0,
            0.0,
        ),
        "volatility_shock": (
            "Volatility shock",
            "Raises annual volatility by 35% while leaving directional evidence unchanged.",
            1.35,
            0.0,
        ),
        "bullish_tape": (
            "Bullish tape",
            "Adds 12 percentage points of annualised drift without changing volatility.",
            1.0,
            0.12,
        ),
        "bearish_tape": (
            "Bearish tape",
            "Subtracts 12 percentage points of annualised drift without changing volatility.",
            1.0,
            -0.12,
        ),
    }

    def simulate(
        self,
        market: MarketSnapshot,
        plan: TradePlan,
        paths: int = 5_000,
        *,
        scenario: Literal["base", "volatility_shock", "bullish_tape", "bearish_tape"] = "base",
        proposal_status: Literal["executable", "research_only", "risk_blocked"] = "executable",
        eligibility_reason: str = "Current proposal cleared the strategy evidence threshold.",
    ) -> MonteCarloResult:
        if plan.strategy == StrategyKind.NO_TRADE or not plan.legs:
            raise ValueError(f"{market.symbol} has no executable structure to simulate")
        if plan.limit_price is None or plan.quantity < 1:
            raise ValueError("The proposal needs a priced, non-zero option structure")

        paths = max(1_000, min(20_000, paths))
        horizon_days = max(1, plan.dte)
        steps = min(30, horizon_days)
        dt = (horizon_days / 365.0) / steps
        spot = market.underlying_price
        contract_ivs = [
            contract.implied_volatility
            for contract in market.contracts
            if contract.implied_volatility > 0
        ]
        implied_vol = statistics.median(contract_ivs) if contract_ivs else 0.0
        base_volatility = min(1.25, max(0.05, market.realized_volatility, implied_vol))
        base_drift = max(
            -0.15,
            min(0.15, market.trend_score * 0.10 + market.news_sentiment * 0.03),
        )
        if scenario not in self._SCENARIOS:
            raise ValueError(f"Unknown simulation scenario: {scenario}")
        scenario_label, scenario_description, volatility_multiplier, drift_shift = self._SCENARIOS[scenario]
        annual_volatility = min(1.50, max(0.05, base_volatility * volatility_multiplier))
        annual_drift = max(-0.30, min(0.30, base_drift + drift_shift))
        seed_material = (
            f"{market.symbol}|{plan.strategy.value}|{spot:.4f}|{horizon_days}|"
            f"{annual_volatility:.5f}|{annual_drift:.5f}|{paths}|{scenario}"
        )
        seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        path_prices: list[list[float]] = [[] for _ in range(steps + 1)]
        pnl_values: list[float] = []
        terminal_prices: list[float] = []
        slippage = self._estimated_slippage(plan)

        for _ in range(paths):
            price = spot
            path_prices[0].append(price)
            for step in range(1, steps + 1):
                shock = rng.gauss(0.0, 1.0)
                price *= math.exp(
                    (annual_drift - 0.5 * annual_volatility**2) * dt
                    + annual_volatility * math.sqrt(dt) * shock
                )
                path_prices[step].append(price)
            terminal_prices.append(price)
            pnl_values.append(self._expiry_pnl(plan, price) - slippage)

        pnl_values.sort()
        terminal_prices.sort()
        tail_count = max(1, math.ceil(paths * 0.05))
        max_loss_threshold = -max(0.01, plan.max_loss) * 0.90
        bands: list[PercentileBandPoint] = []
        for index, prices in enumerate(path_prices):
            prices.sort()
            day = round(horizon_days * index / steps)
            bands.append(
                PercentileBandPoint(
                    day=day,
                    p10=round(_percentile(prices, 0.10), 2),
                    p25=round(_percentile(prices, 0.25), 2),
                    p50=round(_percentile(prices, 0.50), 2),
                    p75=round(_percentile(prices, 0.75), 2),
                    p90=round(_percentile(prices, 0.90), 2),
                )
            )

        pnl_percentiles = {
            f"p{percentile}": round(_percentile(pnl_values, percentile / 100), 2)
            for percentile in (5, 25, 50, 75, 95)
        }
        terminal_percentiles = {
            f"p{percentile}": round(_percentile(terminal_prices, percentile / 100), 2)
            for percentile in (5, 25, 50, 75, 95)
        }
        break_even_lower, break_even_upper = self._break_evens(plan, slippage)
        outcomes = self._outcome_scenarios(
            plan,
            terminal_prices,
            break_even_lower,
            break_even_upper,
            slippage,
        )
        checkpoint_indexes = sorted({0, round(steps * 0.25), round(steps * 0.50), round(steps * 0.75), steps})
        checkpoints = []
        for index in checkpoint_indexes:
            prices = path_prices[index]
            profitable = sum(self._expiry_pnl(plan, price) - slippage > 0 for price in prices)
            checkpoints.append(
                ProbabilityCheckpoint(
                    day=round(horizon_days * index / steps),
                    median_price=round(_percentile(prices, 0.50), 2),
                    probability_profitable_if_expiry=round(profitable / paths, 4),
                )
            )
        return MonteCarloResult(
            symbol=market.symbol,
            strategy=plan.strategy,
            plan=plan,
            spot_price=round(spot, 2),
            expiration=plan.legs[0].expiration,
            horizon_days=horizon_days,
            paths=paths,
            seed=seed,
            annual_drift=round(annual_drift, 4),
            annual_volatility=round(annual_volatility, 4),
            estimated_slippage=round(slippage, 2),
            scenario=scenario,
            scenario_label=scenario_label,
            scenario_description=scenario_description,
            proposal_status=proposal_status,
            eligibility_reason=eligibility_reason,
            expected_pnl=round(statistics.fmean(pnl_values), 2),
            median_pnl=round(_percentile(pnl_values, 0.50), 2),
            probability_profit=round(sum(value > 0 for value in pnl_values) / paths, 4),
            probability_near_max_loss=round(
                sum(value <= max_loss_threshold for value in pnl_values) / paths,
                4,
            ),
            value_at_risk_95=round(max(0.0, -_percentile(pnl_values, 0.05)), 2),
            expected_shortfall_95=round(max(0.0, -statistics.fmean(pnl_values[:tail_count])), 2),
            pnl_percentiles=pnl_percentiles,
            terminal_price_percentiles=terminal_percentiles,
            break_even_lower=break_even_lower,
            break_even_upper=break_even_upper,
            outcome_scenarios=outcomes,
            probability_checkpoints=checkpoints,
            underlying_bands=bands,
            distribution=self._histogram(pnl_values),
            assumptions=[
                "Underlying paths use geometric Brownian motion; returns are not guaranteed to be normally distributed in real markets.",
                f"Annual volatility uses the higher of realized volatility and median chain IV ({annual_volatility:.1%}).",
                f"Annual drift is a bounded translation of current trend and news evidence ({annual_drift:+.1%}).",
                "Option legs settle at expiration intrinsic value; early exercise, dividends and changing volatility are not modelled.",
                f"Estimated half-spread cost of ${slippage:,.2f} is deducted from every outcome.",
                "This is a repeatable stress test of the current proposal, not a price forecast or investment advice.",
            ],
        )

    @classmethod
    def _break_evens(cls, plan: TradePlan, slippage: float) -> tuple[float | None, float | None]:
        if plan.limit_price is None or not plan.legs:
            return None, None
        slippage_per_share = slippage / max(100 * plan.quantity, 100)
        premium = max(0.0, plan.limit_price)

        if plan.strategy in {StrategyKind.LONG_STRADDLE, StrategyKind.LONG_STRANGLE}:
            debit = premium + slippage_per_share
            call = next((leg for leg in plan.legs if leg.option_type == "call" and leg.side == "buy"), None)
            put = next((leg for leg in plan.legs if leg.option_type == "put" and leg.side == "buy"), None)
            return (
                round(put.strike - debit, 2) if put else None,
                round(call.strike + debit, 2) if call else None,
            )
        if plan.strategy in {StrategyKind.BULL_CALL_SPREAD, StrategyKind.LONG_CALL}:
            long_call = next((leg for leg in plan.legs if leg.option_type == "call" and leg.side == "buy"), None)
            return (round(long_call.strike + premium + slippage_per_share, 2) if long_call else None, None)
        if plan.strategy in {StrategyKind.BEAR_PUT_SPREAD, StrategyKind.LONG_PUT}:
            long_put = next((leg for leg in plan.legs if leg.option_type == "put" and leg.side == "buy"), None)
            return (None, round(long_put.strike - premium - slippage_per_share, 2) if long_put else None)
        if plan.strategy == StrategyKind.IRON_CONDOR:
            credit = max(0.0, premium - slippage_per_share)
            short_put = next((leg for leg in plan.legs if leg.option_type == "put" and leg.side == "sell"), None)
            short_call = next((leg for leg in plan.legs if leg.option_type == "call" and leg.side == "sell"), None)
            return (
                round(short_put.strike - credit, 2) if short_put else None,
                round(short_call.strike + credit, 2) if short_call else None,
            )
        return None, None

    @classmethod
    def _outcome_scenarios(
        cls,
        plan: TradePlan,
        terminal_prices: list[float],
        lower: float | None,
        upper: float | None,
        slippage: float,
    ) -> list[OutcomeScenario]:
        total = max(1, len(terminal_prices))

        def is_profitable(price: float) -> bool:
            return cls._expiry_pnl(plan, price) - slippage > 0

        def row(key: str, label: str, condition: str, prices: list[float], representative: float) -> OutcomeScenario:
            profitable = is_profitable(representative)
            return OutcomeScenario(
                key=key,
                label=label,
                condition=condition,
                probability=round(len(prices) / total, 4),
                profitable_at_expiry=profitable,
                interpretation=(
                    "This region clears the strategy's estimated expiry break-even after spread cost."
                    if profitable
                    else "This region does not clear the strategy's estimated expiry break-even after spread cost."
                ),
            )

        if lower is not None and upper is not None:
            below = [price for price in terminal_prices if price < lower]
            inside = [price for price in terminal_prices if lower <= price <= upper]
            above = [price for price in terminal_prices if price > upper]
            return [
                row("below", "Downside break", f"Below ${lower:,.2f}", below, lower * 0.95),
                row("inside", "Between break-evens", f"${lower:,.2f} to ${upper:,.2f}", inside, (lower + upper) / 2),
                row("above", "Upside break", f"Above ${upper:,.2f}", above, upper * 1.05),
            ]
        if lower is not None:
            below = [price for price in terminal_prices if price < lower]
            above = [price for price in terminal_prices if price >= lower]
            return [
                row("below", "Below break-even", f"Below ${lower:,.2f}", below, lower * 0.95),
                row("above", "Above break-even", f"At or above ${lower:,.2f}", above, lower * 1.05),
            ]
        if upper is not None:
            below = [price for price in terminal_prices if price <= upper]
            above = [price for price in terminal_prices if price > upper]
            return [
                row("below", "Below break-even", f"At or below ${upper:,.2f}", below, upper * 0.95),
                row("above", "Above break-even", f"Above ${upper:,.2f}", above, upper * 1.05),
            ]
        return []

    @classmethod
    def _expiry_pnl(cls, plan: TradePlan, terminal_price: float) -> float:
        intrinsic_value = 0.0
        for leg in plan.legs:
            intrinsic = (
                max(terminal_price - leg.strike, 0.0)
                if leg.option_type == "call"
                else max(leg.strike - terminal_price, 0.0)
            )
            direction = 1 if leg.side == "buy" else -1
            intrinsic_value += direction * intrinsic * 100 * leg.ratio_qty

        entry_premium = plan.limit_price * 100
        if plan.strategy in cls._CREDIT_STRATEGIES:
            structure_pnl = intrinsic_value + entry_premium
        else:
            structure_pnl = intrinsic_value - entry_premium
        return structure_pnl * plan.quantity

    @staticmethod
    def _estimated_slippage(plan: TradePlan) -> float:
        half_spreads = [
            max(0.0, leg.mid * (leg.spread_pct / 100.0) * 0.5 * 100 * leg.ratio_qty)
            for leg in plan.legs
        ]
        return sum(half_spreads) * plan.quantity

    @staticmethod
    def _histogram(values: list[float], bins: int = 13) -> list[HistogramBin]:
        low, high = values[0], values[-1]
        if math.isclose(low, high):
            return [HistogramBin(lower=round(low, 2), upper=round(high, 2), count=len(values), probability=1.0)]
        width = (high - low) / bins
        counts = [0] * bins
        for value in values:
            index = min(bins - 1, int((value - low) / width))
            counts[index] += 1
        return [
            HistogramBin(
                lower=round(low + index * width, 2),
                upper=round(low + (index + 1) * width, 2),
                count=count,
                probability=round(count / len(values), 5),
            )
            for index, count in enumerate(counts)
        ]
