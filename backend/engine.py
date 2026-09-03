"""End-to-end autonomous options decision engine."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agents.committee import ReasoningCommittee
from config import ROOT, Settings
from ledger import DecisionLedger, ExecutionLedger
from learning import LearningEngine
from market_universe import MARKET_GROUPS
from models import (
    AgentOpinion,
    AgentStage,
    DashboardState,
    DecisionPassport,
    DecisionStatus,
    ExecutionEvent,
    ExecutionReceipt,
    GateResult,
    MarketBoardItem,
    MarketBreadth,
    MarketPulseState,
    RequirementCheck,
    StrategyKind,
    TradePlan,
)
from risk import RiskConstitution
from runtime_state import RuntimeStateStore
from services.alpaca_rest import AlpacaPaperExecutor
from services.intelligence import IntelligenceProvider
from services.market_provider import MarketProvider
from services.monte_carlo import MonteCarloSimulator
from strategy import StrategyRouter


class VolitionEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = MarketProvider(settings)
        self.router = StrategyRouter()
        self.risk = RiskConstitution(settings)
        self.committee = ReasoningCommittee(settings)
        self.executor = AlpacaPaperExecutor(settings)
        self.intelligence_provider = IntelligenceProvider(settings)
        self.simulator = MonteCarloSimulator()
        self.learning_engine = LearningEngine()
        self.ledger = DecisionLedger(ROOT / "backend" / "data" / "decisions.jsonl")
        self.execution_ledger = ExecutionLedger(ROOT / "backend" / "data" / "order_events.jsonl")
        self.runtime_store = RuntimeStateStore(ROOT / "backend" / "data" / "runtime_state.json")
        self.kill_switch = self.runtime_store.state.kill_switch
        self.autonomous = True
        self._cycle_lock = asyncio.Lock()

    async def dashboard(self) -> DashboardState:
        if self.settings.volition_mode == "paper" and self.settings.paper_credentials_configured:
            await self.reconcile_orders()
        account, market_clock = await asyncio.gather(
            self.provider.account(),
            self.provider.clock(),
        )
        scan_shortlist, cooldown_symbols = await self._trading_shortlist()
        markets = await self.provider.markets(market_clock.is_open, scan_shortlist)
        positions, equity_curve = await asyncio.gather(
            self.provider.positions(account.equity),
            self.provider.portfolio_history(account),
        )
        account.open_positions = len(positions)
        account.open_risk = round(sum(position.max_loss for position in positions), 2)
        opportunities = [
            self.router.route(market, account, self.settings.max_risk_per_trade_pct)
            for market in markets
        ]
        opportunities.sort(key=lambda plan: (plan.strategy == StrategyKind.NO_TRADE, -plan.confidence))
        decisions = self.ledger.recent(limit=8)
        learning = self.learning_engine.summarize(
            self.ledger.recent(limit=500),
            self.execution_ledger.recent(limit=2_000),
        )
        if self.settings.volition_mode == "demo":
            seeds = self._demo_passports(account, markets, opportunities)
            existing = {(item.symbol, item.status) for item in decisions}
            decisions = (decisions + [item for item in seeds if (item.symbol, item.status) not in existing])[:8]
        else:
            decisions = [item for item in decisions if item.receipt.mode != "demo"]

        competition_start = date.fromisoformat(self.settings.hackathon_start_date)
        fresh_account = (
            self.settings.volition_mode == "paper"
            and bool(self.settings.hackathon_account_id)
            and account.created_at is not None
            and account.created_at.date() >= competition_start
        )

        requirements = [
            RequirementCheck(
                label="Dedicated fresh paper account",
                passed=fresh_account,
                detail=(
                    f"Created {account.created_at.date().isoformat()} · {account.account_id}"
                    if account.created_at
                    else "Account creation date has not been verified"
                ),
            ),
            RequirementCheck(
                label="$100,000 competition start",
                passed=fresh_account and abs(account.starting_balance - 100_000) < 0.01,
                detail=(
                    f"Configured start ${account.starting_balance:,.0f} on a competition-period account"
                    if fresh_account
                    else f"Current account predates {competition_start.isoformat()} and cannot prove a fresh competition start"
                ),
            ),
            RequirementCheck(
                label="Options Level 3",
                passed=self.settings.volition_mode == "paper" and account.options_trading_level >= 3,
                detail=(
                    f"Paper account reports level {account.options_trading_level}"
                    if self.settings.volition_mode == "paper"
                    else "Demo assumption — verify Level 3 on the competition account"
                ),
            ),
            RequirementCheck(
                label="Alpaca CLI evidence plane",
                passed=self.provider.cli.available,
                detail=(
                    "Structured CLI receipts enabled"
                    if self.provider.cli.available
                    else "Read-only REST fallback active locally; install the CLI for judging"
                ),
            ),
            RequirementCheck(
                label="Autonomous options loop",
                passed=self.autonomous and self.settings.can_submit_orders,
                detail=(
                    "Research → structure → risk veto → submit → reconcile → manage exits"
                    if self.settings.can_submit_orders
                    else "Loop is live in review-only mode; paper-order permission remains deliberately locked"
                ),
            ),
            RequirementCheck(
                label="AI reasoning committee",
                passed=self.settings.reasoning_enabled,
                detail=(
                    f"Three independent roles use {self.settings.effective_reasoning_provider or 'model runtime'} "
                    f"{self.settings.effective_reasoning_model}"
                    if self.settings.reasoning_enabled
                    else "Deterministic fallback is active; no model runtime is configured"
                ),
            ),
            RequirementCheck(
                label="Paper-only execution lock",
                passed=self.settings.alpaca_paper_trade,
                detail="Live endpoint is not implemented in this build",
            ),
        ]
        scheduler_state = (
            "paused"
            if self.kill_switch or not self.autonomous
            else "running"
            if market_clock.is_open
            else "waiting_for_market"
        )
        runtime = self.runtime_store.state
        market_board = []
        for market in markets:
            bars = market.price_history[-120:]
            first_close = bars[0].close if bars else market.underlying_price
            last_close = bars[-1].close if bars else market.underlying_price
            change = ((last_close / first_close) - 1) * 100 if first_close else 0.0
            market_board.append(
                MarketBoardItem(
                    symbol=market.symbol,
                    price=round(market.underlying_price, 2),
                    period_change_pct=round(change, 3),
                    trend_score=market.trend_score,
                    realized_volatility=market.realized_volatility,
                    iv_rank=market.iv_rank,
                    regime=market.regime,
                    latest_bar_at=bars[-1].timestamp if bars else None,
                    source=market.price_history_source,
                    bars=bars,
                )
            )
        return DashboardState(
            mode=self.settings.volition_mode,  # type: ignore[arg-type]
            data_label=(
                "SIMULATED DEMO · NO ORDERS SENT"
                if self.settings.volition_mode == "demo"
                else "ALPACA PAPER EVIDENCE · ORDERS ENABLED"
                if self.settings.can_submit_orders
                else "ALPACA PAPER EVIDENCE · ORDERS LOCKED"
            ),
            account=account,
            market_board=market_board,
            opportunities=opportunities,
            positions=positions,
            recent_decisions=decisions,
            equity_curve=equity_curve,
            requirements=requirements,
            agent_stages=self._stages("idle"),
            learning=learning,
            market_clock=market_clock,
            kill_switch=self.kill_switch,
            autonomous=self.autonomous,
            order_submission_enabled=self.settings.can_submit_orders,
            operator_controls_configured=(
                not self.settings.operator_auth_required or bool(self.settings.operator_api_key)
            ),
            market_open=market_clock.is_open,
            scheduler_state=scheduler_state,  # type: ignore[arg-type]
            next_cycle_at=(
                runtime.next_cycle_at.isoformat()
                if runtime.next_cycle_at and scheduler_state != "paused"
                else "At the next regular market session"
                if scheduler_state == "waiting_for_market"
                else "Paused by operator"
            ),
            scheduler_last_heartbeat_at=(
                runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None
            ),
            scheduler_last_cycle_at=(runtime.last_cycle_at.isoformat() if runtime.last_cycle_at else None),
            scheduler_last_cycle_status=runtime.last_cycle_status,
            scheduler_last_cycle_message=runtime.last_cycle_message,
            scheduler_consecutive_failures=runtime.consecutive_failures,
            execution_events=self.execution_ledger.recent(limit=20),
            reasoning_mode=self.settings.reasoning_mode,  # type: ignore[arg-type]
            reasoning_model=(
                f"{self.settings.effective_reasoning_provider or 'Model'} · "
                f"{self.settings.effective_reasoning_model}"
                if self.settings.reasoning_enabled
                else "deterministic-fallback"
            ),
            watchlist=self.settings.watchlist,
            scan_shortlist=scan_shortlist,
            cooldown_symbols=cooldown_symbols,
            deep_scan_limit=self.settings.deep_scan_limit,
        )

    async def _trading_shortlist(self) -> tuple[list[str], list[str]]:
        ranked = await self.provider.ranked_symbols(self.settings.watchlist)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.settings.symbol_cooldown_minutes)
        last_reviewed: dict[str, datetime] = {}
        for decision in self.ledger.recent(limit=200):
            if (
                decision.receipt.mode == "demo"
                and self.settings.volition_mode != "demo"
            ) or decision.symbol not in self.settings.watchlist:
                continue
            reviewed_at = decision.created_at
            if reviewed_at.tzinfo is None:
                reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
            last_reviewed.setdefault(decision.symbol, reviewed_at)
        cooling = {
            symbol
            for symbol, reviewed_at in last_reviewed.items()
            if reviewed_at >= cutoff
        }
        fresh = [symbol for symbol in ranked if symbol not in cooling]
        cooled_by_age = sorted(
            (symbol for symbol in ranked if symbol in cooling),
            key=lambda symbol: last_reviewed[symbol],
        )
        shortlist = (fresh + cooled_by_age)[: self.settings.deep_scan_limit]
        return shortlist, [symbol for symbol in ranked if symbol in cooling]

    async def run_scheduled_cycle(self) -> DecisionPassport | None:
        """Run only when autonomous trading is enabled and the market is open."""

        if self.kill_switch or not self.autonomous:
            self.runtime_store.record_cycle("paused", "Scheduled review skipped because the operator pause is armed.")
            return None
        if not await self.provider.market_open():
            self.runtime_store.record_cycle("market_closed", "Scheduled review checked in; the market is closed.")
            return None
        await self.reconcile_orders()
        await self.manage_positions()
        return await self.run_cycle()

    async def reconcile_orders(self) -> list[ExecutionEvent]:
        """Record broker status changes without rewriting immutable decisions."""

        if self.settings.volition_mode != "paper" or not self.settings.paper_credentials_configured:
            return []
        terminal = {"filled", "canceled", "expired", "rejected", "replaced"}
        latest_by_order: dict[str, ExecutionEvent] = {}
        for event in self.execution_ledger.recent(limit=2_000):
            if event.order_id and event.order_id not in latest_by_order:
                latest_by_order[event.order_id] = event
        changes: list[ExecutionEvent] = []
        for order_id, previous in latest_by_order.items():
            if previous.status.lower() in terminal:
                continue
            try:
                body = await self.executor.order(order_id)
                status = str(body.get("status") or "unknown").lower()
                if status == previous.status.lower():
                    continue
                kind = "exit_update" if previous.kind.startswith("exit") else "order_update"
                event = ExecutionEvent(
                    event_id=f"event-{uuid.uuid4().hex[:16]}",
                    decision_id=previous.decision_id,
                    cycle_id=previous.cycle_id,
                    symbol=previous.symbol,
                    kind=kind,
                    status=status,
                    order_id=order_id,
                    client_order_id=str(body.get("client_order_id") or previous.client_order_id or "") or None,
                    filled_qty=float(body.get("filled_qty") or 0.0),
                    filled_avg_price=(
                        float(body["filled_avg_price"])
                        if body.get("filled_avg_price") not in {None, ""}
                        else None
                    ),
                    message=f"Alpaca reports the paper order as {status}.",
                )
                changes.append(self.execution_ledger.append(event))
            except Exception as exc:  # noqa: BLE001
                self.runtime_store.record_cycle(
                    "reconciliation_warning",
                    f"Order reconciliation will retry after {type(exc).__name__}.",
                    failed=True,
                )
        return changes

    @staticmethod
    def _exit_reason(position) -> str | None:
        if position.max_loss > 0 and position.unrealized_pnl >= position.max_loss * 0.25:
            return "profit target reached (+25% of defined risk)"
        if position.max_loss > 0 and position.unrealized_pnl <= -position.max_loss * 0.20:
            return "loss limit reached (−20% of defined risk)"
        dte = max(0, (date.fromisoformat(position.expiration) - date.today()).days)
        if dte <= 5:
            return "expiration window reached (5 DTE or less)"
        return None

    async def manage_positions(self) -> list[ExecutionEvent]:
        """Apply bounded profit, loss and expiration exits to broker-synced positions."""

        if self.kill_switch or not self.settings.can_submit_orders:
            return []
        account = await self.provider.account()
        positions = await self.provider.positions(account.equity)
        recent = self.execution_ledger.recent(limit=2_000)
        submitted: list[ExecutionEvent] = []
        for position in positions:
            reason = self._exit_reason(position)
            if not reason:
                continue
            lifecycle_key = f"exit:{position.symbol}:{position.expiration}"
            existing = next(
                (
                    event
                    for event in recent
                    if event.cycle_id == lifecycle_key
                    and event.status.lower() not in {"canceled", "expired", "rejected"}
                ),
                None,
            )
            if existing:
                continue
            receipt = await self.executor.close_position(position, lifecycle_key, reason)
            event = ExecutionEvent(
                event_id=f"event-{uuid.uuid4().hex[:16]}",
                cycle_id=lifecycle_key,
                symbol=position.symbol,
                kind="exit_submitted",
                status=receipt.raw_status,
                order_id=receipt.order_id,
                client_order_id=receipt.client_order_id,
                message=receipt.message,
            )
            submitted.append(self.execution_ledger.append(event))
        return submitted

    async def market_pulse(self) -> MarketPulseState:
        items, market_clock = await asyncio.gather(
            self.provider.market_pulse(),
            self.provider.clock(),
        )
        advancing = sum(item.change_pct > 0.02 for item in items)
        declining = sum(item.change_pct < -0.02 for item in items)
        unchanged = max(0, len(items) - advancing - declining)
        average_change = sum(item.change_pct for item in items) / len(items) if items else 0.0
        advance_share = advancing / len(items) if items else 0.0
        decline_share = declining / len(items) if items else 0.0
        tone = (
            "risk-on"
            if average_change >= 0.2 and advance_share >= 0.6
            else "risk-off"
            if average_change <= -0.2 and decline_share >= 0.6
            else "mixed"
        )
        return MarketPulseState(
            source=items[0].source if items else "Market feed unavailable",
            refresh_seconds=60,
            groups=list(MARKET_GROUPS),
            tracked_assets=len(items),
            strategy_eligible=sum(item.symbol in self.settings.watchlist for item in items),
            market_open=market_clock.is_open,
            breadth=MarketBreadth(
                advancing=advancing,
                declining=declining,
                unchanged=unchanged,
                average_change_pct=round(average_change, 2),
                tone=tone,  # type: ignore[arg-type]
            ),
            items=items,
        )

    async def run_cycle(self, symbol: str | None = None) -> DecisionPassport:
        if self._cycle_lock.locked():
            raise ValueError("A market review is already running; duplicate cycle blocked.")
        async with self._cycle_lock:
            try:
                decision = await self._run_cycle(symbol)
                self.runtime_store.record_cycle(
                    decision.status.value,
                    f"{decision.symbol} review recorded as {decision.status.value}.",
                )
                return decision
            except Exception as exc:
                self.runtime_store.record_cycle(
                    "failed",
                    f"Review failed safely with {type(exc).__name__}; no retry order was inferred.",
                    failed=True,
                )
                raise

    async def _run_cycle(self, symbol: str | None = None) -> DecisionPassport:
        cycle_id = f"cycle-{uuid.uuid4().hex[:14]}"
        account = await self.provider.account()
        if symbol:
            requested = symbol.upper()
            if requested not in self.settings.watchlist:
                raise ValueError(f"{symbol.upper()} is not in the configured watchlist")
            scan_symbols = [requested]
            cooldown_symbols: list[str] = []
        else:
            scan_symbols, cooldown_symbols = await self._trading_shortlist()
        markets = await self.provider.markets(symbols=scan_symbols)
        if not markets:
            raise ValueError("No shortlisted market evidence was available")

        plans = [self.router.route(market, account, self.settings.max_risk_per_trade_pct) for market in markets]
        candidate_indexes = [index for index, plan in enumerate(plans) if plan.strategy != StrategyKind.NO_TRADE]
        risk_eligible = [
            index
            for index in candidate_indexes
            if self.risk.approved(self.risk.evaluate(plans[index], account, markets[index], self.kill_switch))
        ]
        selection_pool = risk_eligible or candidate_indexes or list(range(len(plans)))
        selected_index = max(selection_pool, key=lambda index: plans[index].confidence)
        market = markets[selected_index]
        plan = plans[selected_index]
        gates = self.risk.evaluate(plan, account, market, self.kill_switch)
        simulation = None
        if plan.strategy != StrategyKind.NO_TRADE:
            try:
                simulation = self.simulator.simulate(market, plan, 2_500)
                simulation_passed = (
                    simulation.expected_pnl > 0
                    and simulation.probability_profit >= 0.35
                    and simulation.probability_near_max_loss <= 0.60
                )
                gates.append(
                    GateResult(
                        name="strategy stress test",
                        passed=simulation_passed,
                        observed=(
                            f"PoP {simulation.probability_profit:.1%} · expected P&L "
                            f"${simulation.expected_pnl:,.0f} · near-max-loss "
                            f"{simulation.probability_near_max_loss:.1%}"
                        ),
                        limit="positive expected P&L; PoP ≥ 35%; near-max-loss probability ≤ 60%",
                    )
                )
            except (ValueError, ArithmeticError):
                gates.append(
                    GateResult(
                        name="strategy stress test",
                        passed=False,
                        observed="A reproducible strategy simulation was unavailable",
                        limit="a complete 2,500-path base stress test is required",
                    )
                )

        opinions = await self.committee.deliberate(market, plan, account, simulation)

        pending_order = self._pending_order_for(plan.symbol)
        gates.append(
            GateResult(
                name="duplicate order lock",
                passed=pending_order is None,
                observed=(
                    "no pending broker order"
                    if pending_order is None
                    else f"{pending_order.status} order {pending_order.order_id} is still active"
                ),
                limit="at most one active entry order per symbol",
            )
        )
        committee_support = self.committee.supports(plan, opinions)
        gates.append(
            GateResult(
                name="AI committee consensus",
                passed=committee_support,
                observed=(
                    f"{sum(opinion.verdict == 'support' for opinion in opinions)} support / "
                    f"{sum(opinion.verdict == 'oppose' for opinion in opinions)} oppose"
                ),
                limit="advisory target: at least 2 support; at most 1 oppose",
                severity="warning",
            )
        )
        approved = self.risk.approved(gates)

        if plan.strategy == StrategyKind.NO_TRADE:
            receipt = await self.executor.execute(plan, cycle_id)
            status = DecisionStatus.NO_TRADE
        elif approved:
            receipt = await self.executor.execute(plan, cycle_id)
            status = (
                DecisionStatus.EXECUTED
                if receipt.mode == "demo" and receipt.accepted
                else DecisionStatus.SUBMITTED
                if receipt.mode == "paper" and receipt.accepted
                else DecisionStatus.PREVIEWED
                if receipt.accepted
                else DecisionStatus.FAILED
            )
        else:
            receipt = ExecutionReceipt(
                mode="demo" if self.settings.volition_mode == "demo" else "preview",
                accepted=False,
                raw_status="risk_veto",
                command_evidence=[
                    "Alpaca CLI evidence gathered",
                    "Trading API submission blocked before POST /v2/orders",
                ],
                message="The deterministic risk constitution or stress test vetoed this proposal. No order was sent.",
            )
            status = DecisionStatus.REJECTED

        passport = DecisionPassport(
            decision_id=f"decision-{uuid.uuid4().hex[:16]}",
            cycle_id=cycle_id,
            symbol=market.symbol,
            status=status,
            plan=plan,
            opinions=opinions,
            gates=gates,
            receipt=receipt,
            evidence={
                "market_source": market.source,
                "account_source": account.source,
                "underlying_price": market.underlying_price,
                "trend_score": market.trend_score,
                "realized_volatility": market.realized_volatility,
                "iv_score": market.iv_rank,
                "regime": market.regime,
                "screened_universe_size": len(self.settings.watchlist),
                "deep_scan_symbols": ",".join(scan_symbols),
                "cooldown_symbols": ",".join(cooldown_symbols),
                "simulation_probability_profit": simulation.probability_profit if simulation else 0.0,
                "simulation_expected_pnl": simulation.expected_pnl if simulation else 0.0,
                "simulation_near_max_loss_probability": simulation.probability_near_max_loss if simulation else 0.0,
            },
        )
        passport = self.ledger.append(passport)
        lifecycle_kind = "order_submitted" if passport.status == DecisionStatus.SUBMITTED else "review"
        self.execution_ledger.append(
            ExecutionEvent(
                event_id=f"event-{uuid.uuid4().hex[:16]}",
                decision_id=passport.decision_id,
                cycle_id=passport.cycle_id,
                symbol=passport.symbol,
                kind=lifecycle_kind,
                status=passport.receipt.raw_status,
                order_id=passport.receipt.order_id,
                client_order_id=passport.receipt.client_order_id,
                message=passport.receipt.message,
            )
        )
        return passport

    def _pending_order_for(self, symbol: str) -> ExecutionEvent | None:
        if self.settings.volition_mode != "paper":
            return None
        terminal = {"filled", "canceled", "expired", "rejected", "replaced"}
        seen: set[str] = set()
        for event in self.execution_ledger.recent(limit=2_000):
            if not event.order_id or event.order_id in seen:
                continue
            seen.add(event.order_id)
            if (
                event.symbol == symbol
                and event.kind in {"order_submitted", "order_update"}
                and event.status.lower() not in terminal
            ):
                return event
        return None

    def set_kill_switch(self, armed: bool) -> bool:
        self.kill_switch = armed
        self.runtime_store.set_kill_switch(armed)
        return self.kill_switch

    def decision(self, decision_id: str) -> DecisionPassport | None:
        return next(
            (item for item in self.ledger.recent(limit=500) if item.decision_id == decision_id),
            None,
        )

    async def intelligence(self):
        return await self.intelligence_provider.snapshot(self.settings.watchlist)

    def learning(self):
        return self.learning_engine.summarize(
            self.ledger.recent(limit=500),
            self.execution_ledger.recent(limit=2_000),
        )

    async def strategy_lab(
        self,
        symbol: str | None = None,
        paths: int = 5_000,
        scenario: str = "base",
    ):
        account = await self.provider.account()
        if symbol:
            requested = symbol.upper()
            if requested not in self.settings.watchlist:
                raise ValueError(f"{requested} is not in the configured watchlist")
            scan_symbols = [requested]
        else:
            scan_symbols, _ = await self._trading_shortlist()
        markets = await self.provider.markets(symbols=scan_symbols)
        plans = [
            self.router.route(market, account, self.settings.max_risk_per_trade_pct)
            for market in markets
        ]
        if symbol:
            matches = [
                (market, plan)
                for market, plan in zip(markets, plans)
                if market.symbol.upper() == requested
            ]
            if not matches:
                raise ValueError(f"{requested} is not in the configured watchlist")
            market, plan = matches[0]
        else:
            candidates = [
                (market, plan)
                for market, plan in zip(markets, plans)
                if plan.strategy != StrategyKind.NO_TRADE
            ]
            if not candidates:
                raise ValueError("No current proposal cleared the strategy evidence threshold")
            market, plan = max(candidates, key=lambda item: item[1].confidence)

        proposal_status = "executable"
        eligibility_reason = "Current proposal cleared the strategy evidence threshold."
        if plan.strategy == StrategyKind.NO_TRADE:
            eligibility_reason = plan.no_trade_reason or "Current evidence says hold cash."
            plan = self.router.research_candidate(market)
            proposal_status = "research_only"
        if plan.strategy == StrategyKind.NO_TRADE:
            raise ValueError(f"{market.symbol} has no complete defined-risk structure to simulate")
        result = self.simulator.simulate(
            market,
            plan,
            paths,
            scenario=scenario,
            proposal_status=proposal_status,
            eligibility_reason=eligibility_reason,
        )
        if proposal_status == "executable":
            lab_market = market.model_copy(update={"market_open": True})
            gates = self.risk.evaluate(plan, account, lab_market, False)
            blocked = [gate for gate in gates if not gate.passed and gate.severity == "block"]
            simulation_passed = (
                result.expected_pnl > 0
                and result.probability_profit >= 0.35
                and result.probability_near_max_loss <= 0.60
            )
            if blocked or not simulation_passed:
                reasons = [f"{gate.name}: {gate.observed}" for gate in blocked]
                if not simulation_passed:
                    reasons.append(
                        "stress test: requires positive expected P&L, at least 35% probability of profit, "
                        "and no more than 60% near-max-loss probability"
                    )
                result = result.model_copy(
                    update={
                        "proposal_status": "risk_blocked",
                        "eligibility_reason": " · ".join(reasons),
                    }
                )
        return result

    def _demo_passports(self, account, markets, opportunities: list[TradePlan]) -> list[DecisionPassport]:
        by_symbol = {plan.symbol: plan for plan in opportunities}
        market_by_symbol = {market.symbol: market for market in markets}
        rows: list[DecisionPassport] = []
        specs = [
            ("SPY", DecisionStatus.EXECUTED, "simulated_fill"),
            ("AAPL", DecisionStatus.NO_TRADE, "no_trade"),
            ("NVDA", DecisionStatus.PREVIEWED, "risk_approved_not_submitted"),
        ]
        previous = "GENESIS"
        for index, (symbol, status, raw_status) in enumerate(specs):
            plan = by_symbol[symbol]
            market = market_by_symbol[symbol]
            opinions = [
                AgentOpinion(
                    agent="Regime Sentinel",
                    verdict="support",
                    confidence=0.84,
                    summary=f"The {market.regime} evidence is internally consistent with the capital decision.",
                    evidence=[f"trend {market.trend_score:+.2f}", f"IV score {market.iv_rank:.0f}"],
                ),
                AgentOpinion(
                    agent="Volatility Architect",
                    verdict="support",
                    confidence=0.79,
                    summary="The proposed premium exposure matches the observed volatility regime.",
                    evidence=[f"realized vol {market.realized_volatility:.1%}", f"{plan.dte} DTE"],
                ),
                AgentOpinion(
                    agent="Adversarial Skeptic",
                    verdict="support" if status != DecisionStatus.PREVIEWED else "abstain",
                    confidence=0.73,
                    summary="Risk is finite and the deterministic constitution remains the final authority.",
                    evidence=[f"max loss ${plan.max_loss:,.0f}", "no naked exposure"],
                ),
            ]
            gates = self.risk.evaluate(plan, account, market, False)
            gates.append(
                GateResult(
                    name="AI committee consensus",
                    passed=True,
                    observed="3 support / 0 oppose",
                    limit="advisory target: at least 2 support; at most 1 oppose",
                    severity="warning",
                )
            )
            decision_id = f"demo-decision-{index + 1}"
            receipt = ExecutionReceipt(
                mode="demo" if status != DecisionStatus.PREVIEWED else "preview",
                accepted=status != DecisionStatus.NO_TRADE,
                order_id=f"demo-order-{index + 1}" if status == DecisionStatus.EXECUTED else None,
                raw_status=raw_status,
                command_evidence=[
                    f"Alpaca CLI · data option chain --underlying-symbol {symbol}",
                    "Demo evidence · explicitly simulated",
                ],
                message=(
                    "Simulated execution receipt."
                    if status == DecisionStatus.EXECUTED
                    else plan.no_trade_reason or "Approved preview; order submission disabled."
                ),
            )
            digest = hashlib.sha256(f"{previous}:{decision_id}:{symbol}:{status}".encode()).hexdigest()
            rows.append(
                DecisionPassport(
                    decision_id=decision_id,
                    cycle_id=f"demo-cycle-{index + 1}",
                    symbol=symbol,
                    status=status,
                    plan=plan,
                    opinions=opinions,
                    gates=gates,
                    receipt=receipt,
                    evidence={
                        "market_source": "demo-simulated",
                        "underlying_price": market.underlying_price,
                        "regime": market.regime,
                        "iv_score": market.iv_rank,
                    },
                    previous_hash=previous,
                    record_hash=digest,
                )
            )
            previous = digest
        return rows

    @staticmethod
    def _stages(status: str) -> list[AgentStage]:
        return [
            AgentStage(key="evidence", label="Evidence Scout", status=status, detail="Account, bars, news, option chains"),
            AgentStage(key="regime", label="Regime Sentinel", status=status, detail="Trend and volatility classification"),
            AgentStage(key="committee", label="AI Committee", status=status, detail="Independent support / oppose debate"),
            AgentStage(key="structure", label="Options Architect", status=status, detail="Concrete OCC symbols and payoff"),
            AgentStage(key="risk", label="Risk Constitution", status=status, detail="Deterministic veto authority"),
            AgentStage(key="execution", label="Alpaca Executor", status=status, detail="Atomic paper mleg + receipt"),
        ]
