"""End-to-end autonomous options decision engine."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agents.committee import ReasoningCommittee
from audit import AuditTrail
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
        self.router = StrategyRouter(settings.bearish_spreads_enabled, settings.condor_symbols)
        self.risk = RiskConstitution(settings)
        self.audit = AuditTrail(ROOT / "backend" / "data" / "audit.sqlite3", sensitive_values=[
            settings.alpaca_api_key, settings.alpaca_secret_key, settings.reasoning_api_key,
            settings.featherless_api_key, settings.marketaux_api_token, settings.fred_api_key,
            settings.operator_api_key,
        ])
        self.committee = ReasoningCommittee(settings, self.audit)
        self.executor = AlpacaPaperExecutor(settings)
        self.intelligence_provider = IntelligenceProvider(settings)
        self.simulator = MonteCarloSimulator(settings.credit_vol_factor)
        self.learning_engine = LearningEngine()
        self.ledger = DecisionLedger(ROOT / "backend" / "data" / "decisions.jsonl")
        self.execution_ledger = ExecutionLedger(ROOT / "backend" / "data" / "order_events.jsonl")
        self.runtime_store = RuntimeStateStore(ROOT / "backend" / "data" / "runtime_state.json")
        self.kill_switch = self.runtime_store.state.kill_switch
        self.autonomous = True
        self._cycle_lock = asyncio.Lock()

    def _operational_audit(self, kind: str, payload: object, *, cycle_id: str | None = None,
                           symbol: str | None = None) -> None:
        """Audit failures must not block a risk-reducing exit or emergency pause."""
        try:
            self.audit.append(kind, payload, cycle_id=cycle_id, symbol=symbol)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("volition.audit").error(
                "Audit write failed during %s: %s", kind, type(exc).__name__
            )

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
        account.open_structures = sum(position.structure_count for position in positions)
        account.open_risk = round(sum(position.max_loss for position in positions), 2)
        account.open_risk_limit_pct = self.settings.max_portfolio_open_risk_pct
        opportunities = [
            self.router.route(market, account, self.settings.max_risk_per_trade_pct)
            for market in markets
        ]
        opportunities.sort(key=lambda plan: (plan.strategy == StrategyKind.NO_TRADE, -plan.confidence))
        decisions = self.ledger.recent(limit=8)
        learning = self.learning_engine.summarize(
            # Full history: exits close entries placed weeks earlier.
            self.ledger.recent(limit=1_000_000),
            self.execution_ledger.recent(limit=1_000_000),
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
            "disabled"
            if self.settings.volition_mode != "paper" or not self.settings.scheduler_enabled
            else "paused"
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
        unresolved_orders = [
            event for event in self.execution_ledger.active_orders()
            if event.order_id is None and event.client_order_id
        ]
        recent_events = self.execution_ledger.recent(limit=20)
        unresolved_ids = {event.event_id for event in unresolved_orders}
        visible_events = (
            unresolved_orders
            + [event for event in recent_events if event.event_id not in unresolved_ids]
        )[:20]
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
                if runtime.next_cycle_at and scheduler_state in {"running", "waiting_for_market"}
                else "At the next regular market session"
                if scheduler_state == "waiting_for_market"
                else "Autonomous scheduler disabled; automated exits are not running"
                if scheduler_state == "disabled"
                else "New entries paused; position exits are managed during market hours"
            ),
            scheduler_last_heartbeat_at=(
                runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None
            ),
            scheduler_last_cycle_at=(runtime.last_cycle_at.isoformat() if runtime.last_cycle_at else None),
            scheduler_last_cycle_status=runtime.last_cycle_status,
            scheduler_last_cycle_message=runtime.last_cycle_message,
            scheduler_consecutive_failures=runtime.consecutive_failures,
            execution_events=visible_events,
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

    async def _trading_shortlist(self, audit_cycle_id: str | None = None) -> tuple[list[str], list[str]]:
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
        if audit_cycle_id:
            self.audit.append("universe_screen", {
                "watchlist": self.settings.watchlist, "market_pulse": await self.provider.market_pulse(),
                "ranked": ranked, "cooldown_last_reviewed": last_reviewed,
                "shortlist": shortlist, "cooling": sorted(cooling),
            }, cycle_id=audit_cycle_id)
        return shortlist, [symbol for symbol in ranked if symbol in cooling]

    async def run_scheduled_cycle(self) -> DecisionPassport | None:
        """Manage existing risk whenever open; pause only new entries."""

        tick_id = f"tick-{uuid.uuid4().hex[:14]}"
        market_open = await self.provider.market_open()
        self._operational_audit("scheduler_tick", {"market_open": market_open, "kill_switch": self.kill_switch,
                                                  "autonomous": self.autonomous}, cycle_id=tick_id)
        if not market_open:
            self.runtime_store.record_cycle("market_closed", "Scheduled review checked in; the market is closed.")
            return None
        await self.reconcile_orders(audit_cycle_id=tick_id)
        await self.manage_positions(audit_cycle_id=tick_id)
        if self.kill_switch or not self.autonomous:
            self._operational_audit("scheduler_paused", {"kill_switch": self.kill_switch,
                                                        "autonomous": self.autonomous}, cycle_id=tick_id)
            self.runtime_store.record_cycle("paused", "New entries paused; existing position exits are still managed.")
            return None
        return await self.run_cycle(trigger="scheduled", parent_cycle_id=tick_id)

    async def reconcile_orders(self, audit_cycle_id: str | None = None) -> list[ExecutionEvent]:
        """Record broker status changes without rewriting immutable decisions."""

        if self.settings.volition_mode != "paper" or not self.settings.paper_credentials_configured:
            return []
        changes: list[ExecutionEvent] = []
        for previous in self.execution_ledger.active_orders():
            try:
                body = (
                    await self.executor.order(previous.order_id)
                    if previous.order_id
                    else await self.executor.order_by_client_order_id(previous.client_order_id)
                )
                self._operational_audit("broker_order_snapshot", {"previous": previous, "broker_response": body},
                                        cycle_id=audit_cycle_id or previous.cycle_id, symbol=previous.symbol)
                status = str(body.get("status") or "unknown").lower()
                order_id = str(body.get("id") or previous.order_id or "") or None
                if status == previous.status.lower() and order_id == previous.order_id:
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
                self._operational_audit("reconciliation_failed", {
                    "previous": previous, "error_type": type(exc).__name__,
                }, cycle_id=audit_cycle_id or previous.cycle_id, symbol=previous.symbol)
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

    def _stress_passed(self, simulation, plan) -> bool:
        floor = self.settings.expected_pnl_floor(plan.strategy.value, plan.symbol, plan.max_loss)
        return (
            simulation.expected_pnl > floor
            and simulation.probability_profit >= 0.35
            and simulation.probability_near_max_loss <= 0.60
        )

    def _stress_limit(self, plan) -> str:
        floor = self.settings.expected_pnl_floor(plan.strategy.value, plan.symbol, plan.max_loss)
        expected = "positive expected P&L" if not floor else f"expected P&L above ${floor:,.0f} (index-ETF condor allowance)"
        return f"{expected}; PoP ≥ 35%; near-max-loss probability ≤ 60%"

    async def manage_positions(self, audit_cycle_id: str | None = None) -> list[ExecutionEvent]:
        """Apply bounded profit, loss and expiration exits to broker-synced positions."""

        if not self.settings.can_submit_orders:
            return []
        account = await self.provider.account()
        positions = await self.provider.positions(account.equity)
        self._operational_audit("position_review_inputs", {"account": account, "positions": positions},
                                cycle_id=audit_cycle_id)
        active_exits = [
            event for event in self.execution_ledger.active_orders()
            if event.kind.startswith("exit")
        ]
        submitted: list[ExecutionEvent] = []
        for position in positions:
            reason = self._exit_reason(position)
            self._operational_audit("position_exit_check", {"position": position, "reason": reason},
                                    cycle_id=audit_cycle_id, symbol=position.symbol)
            if not reason:
                continue
            lifecycle_key = f"exit:{position.symbol}:{position.expiration}"
            active_exit = any(
                event.cycle_id == lifecycle_key for event in active_exits
            )
            if active_exit:
                self._operational_audit("exit_duplicate_blocked", {"position": position, "reason": reason},
                                        cycle_id=audit_cycle_id, symbol=position.symbol)
                continue
            client_order_id = f"volition-exit-{uuid.uuid4().hex[:20]}" if position.raw_legs else None
            if client_order_id:
                self._operational_audit("exit_order_intent", {
                    "position": position, "reason": reason, "client_order_id": client_order_id,
                }, cycle_id=lifecycle_key, symbol=position.symbol)
                self.execution_ledger.append(
                    ExecutionEvent(
                        event_id=f"event-{uuid.uuid4().hex[:16]}",
                        cycle_id=lifecycle_key,
                        symbol=position.symbol,
                        kind="exit_intent",
                        status="submission_unknown",
                        client_order_id=client_order_id,
                        message="Exit intent persisted before contacting Alpaca; broker confirmation is pending.",
                    )
                )
            receipt = await self.executor.close_position(
                position, lifecycle_key, reason, client_order_id=client_order_id
            )
            self._operational_audit("exit_order_receipt", {"reason": reason, "receipt": receipt},
                                    cycle_id=lifecycle_key, symbol=position.symbol)
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

    async def run_cycle(self, symbol: str | None = None, *, trigger: str = "operator",
                        parent_cycle_id: str | None = None) -> DecisionPassport:
        if self._cycle_lock.locked():
            raise ValueError("A market review is already running; duplicate cycle blocked.")
        async with self._cycle_lock:
            cycle_id = f"cycle-{uuid.uuid4().hex[:14]}"
            self.audit.append("cycle_started", {
                "trigger": trigger, "requested_symbol": symbol, "parent_cycle_id": parent_cycle_id,
                "mode": self.settings.volition_mode, "execution_mode": self.settings.execution_mode,
                "reasoning_model": self.settings.effective_reasoning_model,
                "kill_switch": self.kill_switch,
            }, cycle_id=cycle_id, symbol=symbol)
            try:
                decision = await self._run_cycle(symbol, cycle_id)
                self.runtime_store.record_cycle(
                    decision.status.value,
                    f"{decision.symbol} review recorded as {decision.status.value}.",
                )
                return decision
            except Exception as exc:
                self.audit.append("cycle_failed", {"error_type": type(exc).__name__},
                                  cycle_id=cycle_id, symbol=symbol)
                self.runtime_store.record_cycle(
                    "failed",
                    f"Review failed safely with {type(exc).__name__}; no retry order was inferred.",
                    failed=True,
                )
                raise

    async def _run_cycle(self, symbol: str | None = None, cycle_id: str | None = None) -> DecisionPassport:
        cycle_id = cycle_id or f"cycle-{uuid.uuid4().hex[:14]}"
        account = await self.provider.account()
        positions = await self.provider.positions(account.equity)
        account.open_positions = len(positions)
        account.open_structures = sum(position.structure_count for position in positions)
        account.open_risk = round(sum(position.max_loss for position in positions), 2)
        account.open_risk_limit_pct = self.settings.max_portfolio_open_risk_pct
        self.audit.append("portfolio_inputs", {"account": account, "positions": positions}, cycle_id=cycle_id)
        if symbol:
            requested = symbol.upper()
            if requested not in self.settings.watchlist:
                raise ValueError(f"{symbol.upper()} is not in the configured watchlist")
            scan_symbols = [requested]
            cooldown_symbols: list[str] = []
        else:
            scan_symbols, cooldown_symbols = await self._trading_shortlist(audit_cycle_id=cycle_id)
        self.audit.append("scan_selected", {"symbols": scan_symbols, "cooldown_symbols": cooldown_symbols},
                          cycle_id=cycle_id)
        markets = await self.provider.markets(symbols=scan_symbols, audit=self.audit, audit_cycle_id=cycle_id)
        if not markets:
            raise ValueError("No shortlisted market evidence was available")
        self.audit.append("market_snapshots", {"markets": markets}, cycle_id=cycle_id)

        plans = [self.router.route(market, account, self.settings.max_risk_per_trade_pct) for market in markets]
        candidate_indexes = [index for index, plan in enumerate(plans) if plan.strategy != StrategyKind.NO_TRADE]
        candidate_gates = [
            self.risk.evaluate(plan, account, market, self.kill_switch, positions)
            for plan, market in zip(plans, markets)
        ]
        pending_by_index = [self._pending_order_for(plan.symbol) for plan in plans]
        risk_eligible = [
            index
            for index in candidate_indexes
            if self.risk.approved(candidate_gates[index]) and pending_by_index[index] is None
        ]
        self.audit.append("candidate_screen", {
            "candidates": [
                {"symbol": market.symbol, "plan": plan, "gates": gates,
                 "pending_order": pending_by_index[index],
                 "risk_eligible": index in risk_eligible}
                for index, (market, plan, gates) in enumerate(zip(markets, plans, candidate_gates))
            ]
        }, cycle_id=cycle_id)
        selection_pool = risk_eligible or candidate_indexes or list(range(len(plans)))
        selected_index = max(selection_pool, key=lambda index: plans[index].confidence)
        market = markets[selected_index]
        plan = plans[selected_index]
        gates = list(candidate_gates[selected_index])
        self.audit.append("candidate_selected", {
            "selected_index": selected_index, "selection_pool": selection_pool,
            "risk_eligible": risk_eligible, "candidate_indexes": candidate_indexes,
            "plan": plan,
        }, cycle_id=cycle_id, symbol=market.symbol)
        simulation = None
        if plan.strategy != StrategyKind.NO_TRADE:
            try:
                simulation = self.simulator.simulate(market, plan, 2_500)
                self.audit.append("simulation_result", {
                    "paths_requested": 2_500, "scenario": "base", "result": simulation,
                }, cycle_id=cycle_id, symbol=market.symbol)
                simulation_passed = self._stress_passed(simulation, plan)
                gates.append(
                    GateResult(
                        name="strategy stress test",
                        passed=simulation_passed,
                        observed=(
                            f"PoP {simulation.probability_profit:.1%} · expected P&L "
                            f"${simulation.expected_pnl:,.0f} · near-max-loss "
                            f"{simulation.probability_near_max_loss:.1%}"
                        ),
                        limit=self._stress_limit(plan),
                    )
                )
            except (ValueError, ArithmeticError):
                self.audit.append("simulation_failed", {
                    "paths_requested": 2_500, "scenario": "base",
                    "reason": "ValueError or ArithmeticError",
                }, cycle_id=cycle_id, symbol=market.symbol)
                gates.append(
                    GateResult(
                        name="strategy stress test",
                        passed=False,
                        observed="A reproducible strategy simulation was unavailable",
                        limit="a complete 2,500-path base stress test is required",
                    )
                )

        opinions = await self.committee.deliberate(market, plan, account, simulation, cycle_id=cycle_id)

        pending_order = self._pending_order_for(plan.symbol)
        gates.append(
            GateResult(
                name="duplicate order lock",
                passed=pending_order is None,
                observed=(
                    "no pending broker order"
                    if pending_order is None
                    else f"{pending_order.status} order {pending_order.order_id or pending_order.client_order_id} is still active"
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
        self.audit.append("decision_evaluated", {
            "plan": plan, "opinions": opinions, "gates": gates, "approved": approved,
            "pending_order": pending_order,
        }, cycle_id=cycle_id, symbol=market.symbol)

        if plan.strategy == StrategyKind.NO_TRADE:
            receipt = await self.executor.execute(plan, cycle_id)
            status = DecisionStatus.NO_TRADE
        elif approved:
            client_order_id = f"volition-{cycle_id[-14:]}" if self.settings.can_submit_orders else None
            if client_order_id:
                self.audit.append("entry_order_intent", {
                    "order_payload": self.executor.order_payload(plan, client_order_id),
                    "client_order_id": client_order_id,
                }, cycle_id=cycle_id, symbol=plan.symbol)
                self.execution_ledger.append(
                    ExecutionEvent(
                        event_id=f"event-{uuid.uuid4().hex[:16]}",
                        cycle_id=cycle_id,
                        symbol=plan.symbol,
                        kind="order_intent",
                        status="submission_unknown",
                        client_order_id=client_order_id,
                        message="Entry intent persisted before contacting Alpaca; broker confirmation is pending.",
                    )
                )
            receipt = await self.executor.execute(plan, cycle_id, client_order_id=client_order_id)
            self.audit.append("entry_order_receipt", {"receipt": receipt},
                              cycle_id=cycle_id, symbol=plan.symbol)
            status = (
                DecisionStatus.EXECUTED
                if receipt.mode == "demo" and receipt.accepted
                else DecisionStatus.SUBMITTED
                if receipt.mode == "paper" and receipt.accepted
                else DecisionStatus.PREVIEWED
                if receipt.accepted
                else DecisionStatus.UNCERTAIN
                if receipt.raw_status == "submission_unknown"
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
                "open_position_groups": account.open_positions,
                "open_structures": account.open_structures,
                "open_risk": account.open_risk,
                "open_risk_pct": account.open_risk_pct,
                "simulation_probability_profit": simulation.probability_profit if simulation else 0.0,
                "simulation_expected_pnl": simulation.expected_pnl if simulation else 0.0,
                "simulation_near_max_loss_probability": simulation.probability_near_max_loss if simulation else 0.0,
            },
        )
        passport = self.ledger.append(passport)
        self.audit.append("decision_persisted", {
            "decision_id": passport.decision_id, "status": passport.status,
            "record_hash": passport.record_hash, "receipt": passport.receipt,
        }, cycle_id=cycle_id, symbol=market.symbol)
        lifecycle_kind = (
            "order_submitted"
            if passport.status == DecisionStatus.SUBMITTED
            else "order_update"
            if passport.receipt.mode == "paper" and passport.receipt.client_order_id
            else "review"
        )
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
        for event in self.execution_ledger.active_orders():
            if (
                event.symbol == symbol
                and event.kind in {"review", "order_intent", "order_submitted", "order_update"}
            ):
                return event
        return None

    def set_kill_switch(self, armed: bool) -> bool:
        previous = self.kill_switch
        self._operational_audit("operator_kill_switch", {"previous": previous, "requested": armed})
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
            # Full history: exits close entries placed weeks earlier.
            self.ledger.recent(limit=1_000_000),
            self.execution_ledger.recent(limit=1_000_000),
        )

    async def strategy_lab(
        self,
        symbol: str | None = None,
        paths: int = 5_000,
        scenario: str = "base",
    ):
        lab_id = f"lab-{uuid.uuid4().hex[:14]}"
        self.audit.append("strategy_lab_requested", {
            "requested_symbol": symbol, "paths": paths, "scenario": scenario,
        }, cycle_id=lab_id, symbol=symbol)
        account = await self.provider.account()
        if symbol:
            requested = symbol.upper()
            if requested not in self.settings.watchlist:
                raise ValueError(f"{requested} is not in the configured watchlist")
            scan_symbols = [requested]
        else:
            scan_symbols, _ = await self._trading_shortlist(audit_cycle_id=lab_id)
        markets = await self.provider.markets(symbols=scan_symbols, audit=self.audit, audit_cycle_id=lab_id)
        plans = [
            self.router.route(market, account, self.settings.max_risk_per_trade_pct)
            for market in markets
        ]
        self.audit.append("strategy_lab_inputs", {
            "account": account, "markets": markets, "plans": plans,
        }, cycle_id=lab_id)
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
            simulation_passed = self._stress_passed(result, plan)
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
        self.audit.append("strategy_lab_result", {
            "market": market, "plan": plan, "result": result,
        }, cycle_id=lab_id, symbol=market.symbol)
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
