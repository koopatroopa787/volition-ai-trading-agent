"""Evidence accounting for the champion/challenger learning loop.

The learner never fabricates outcomes. Until closed paper trades exist it only
summarises decision coverage, approvals, vetoes and missing evidence.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from models import (
    CommonVeto,
    DecisionPassport,
    DecisionStatus,
    ExecutionEvent,
    LearningState,
    StrategyEvidence,
    StrategyKind,
)


def closed_trades(
    decisions: list[DecisionPassport],
    execution_events: list[ExecutionEvent],
) -> list[dict]:
    """Pair entry fills with the exit fills that closed them.

    Exits are keyed by ``exit:SYMBOL:EXPIRATION`` and carry no decision id, and
    one broker position can hold several entries, so trades are matched by that
    key. ``manage_positions`` always closes the whole position, so an exit fill
    closes every entry filled before it under the same key. Each broker order
    counts once (its last filled event), and cash is ``-100 * price * qty``:
    multi-leg exits may report the whole position as one unit at a summed price.
    """

    by_id = {item.decision_id: item for item in decisions}
    fills: dict[str, ExecutionEvent] = {}
    for event in sorted(execution_events, key=lambda item: item.recorded_at):
        if event.status.lower() == "filled" and event.order_id and event.kind in {
            "order_submitted", "order_update", "exit_submitted", "exit_update",
        }:
            fills[event.order_id] = event

    groups: dict[str, list[ExecutionEvent]] = defaultdict(list)
    for event in fills.values():
        if event.kind.startswith("exit") and event.cycle_id and event.cycle_id.startswith("exit:"):
            groups[event.cycle_id[len("exit:"):]].append(event)
        elif event.kind.startswith("order"):
            decision = by_id.get(event.decision_id or "")
            if decision and decision.plan.legs:
                groups[f"{decision.symbol}:{decision.plan.legs[0].expiration}"].append(event)

    trades: list[dict] = []
    for key, events in groups.items():
        open_entries: list[ExecutionEvent] = []
        for event in sorted(events, key=lambda item: item.recorded_at):
            if event.kind.startswith("order"):
                open_entries.append(event)
                continue
            if not open_entries:
                continue
            cash = -100 * sum((item.filled_avg_price or 0.0) * item.filled_qty for item in [*open_entries, event])
            first = by_id[open_entries[0].decision_id or ""]
            trades.append({
                "key": key,
                "strategy": first.plan.strategy,
                "decision_ids": [item.decision_id for item in open_entries],
                "opened_at": open_entries[0].recorded_at,
                "closed_at": event.recorded_at,
                "pnl": round(cash, 2),
            })
            open_entries = []
    return sorted(trades, key=lambda item: item["closed_at"])


class LearningEngine:
    MINIMUM_OUTCOMES = 5

    def summarize(
        self,
        decisions: list[DecisionPassport],
        execution_events: list[ExecutionEvent] | None = None,
    ) -> LearningState:
        execution_events = execution_events or []
        approved_statuses = {
            DecisionStatus.PREVIEWED,
            DecisionStatus.SUBMITTED,
            DecisionStatus.EXECUTED,
        }
        approved_previews = sum(item.status == DecisionStatus.PREVIEWED for item in decisions)
        filled_entry_orders = {
            item.order_id
            for item in execution_events
            if item.kind in {"order_submitted", "order_update"}
            and item.status.lower() == "filled" and item.order_id
        }
        paper_executions = len(filled_entry_orders) + sum(
            item.status == DecisionStatus.EXECUTED and item.receipt.mode == "paper"
            for item in decisions
        )
        risk_vetoes = sum(item.status == DecisionStatus.REJECTED for item in decisions)
        capital_preserved = sum(item.status == DecisionStatus.NO_TRADE for item in decisions)

        trades = closed_trades(decisions, execution_events)
        closed_outcomes = len(trades)
        closed_entries = sum(len(trade["decision_ids"]) for trade in trades)
        pending_outcomes = max(0, len(filled_entry_orders) - closed_entries)
        realized: dict[StrategyKind, list[float]] = defaultdict(list)
        for trade in trades:
            realized[trade["strategy"]].append(trade["pnl"])
        stage = "collecting_evidence" if closed_outcomes < self.MINIMUM_OUTCOMES else "calibrating"

        grouped: dict[StrategyKind, list[DecisionPassport]] = defaultdict(list)
        veto_counts: Counter[str] = Counter()
        for decision in decisions:
            if decision.plan.strategy != StrategyKind.NO_TRADE:
                grouped[decision.plan.strategy].append(decision)
            veto_counts.update(decision.blocked_by)

        candidates: list[StrategyEvidence] = []
        for strategy, rows in grouped.items():
            gate_rates = [
                sum(gate.passed for gate in row.gates) / len(row.gates)
                for row in rows
                if row.gates
            ]
            candidates.append(
                StrategyEvidence(
                    strategy=strategy,
                    reviewed=len(rows),
                    approved=sum(row.status in approved_statuses for row in rows),
                    vetoed=sum(row.status == DecisionStatus.REJECTED for row in rows),
                    average_confidence=round(sum(row.plan.confidence for row in rows) / len(rows), 4),
                    average_gate_pass_rate=round(sum(gate_rates) / len(gate_rates), 4) if gate_rates else 0.0,
                    realized_trades=len(realized[strategy]),
                    realized_pnl=round(sum(realized[strategy]), 2),
                    win_rate=(
                        round(sum(pnl > 0 for pnl in realized[strategy]) / len(realized[strategy]), 4)
                        if realized[strategy] else None
                    ),
                )
            )
        candidates.sort(key=lambda item: (-item.reviewed, -item.average_gate_pass_rate, item.strategy.value))

        lessons = []
        if closed_outcomes < self.MINIMUM_OUTCOMES:
            lessons.append("No strategy can be promoted until at least five verified closed paper outcomes exist.")
        if trades:
            total = sum(trade["pnl"] for trade in trades)
            wins = sum(trade["pnl"] > 0 for trade in trades)
            lessons.append(
                f"{closed_outcomes} closed paper trade(s) realised ${total:,.0f} with {wins} win(s)."
            )
        if risk_vetoes:
            lessons.append(
                f"The deterministic constitution prevented {risk_vetoes} proposal(s) from becoming orders."
            )
        if veto_counts:
            gate, count = veto_counts.most_common(1)[0]
            lessons.append(f"The most frequent blocking condition is {gate} ({count} review(s)).")
        if capital_preserved:
            lessons.append(f"Cash was deliberately preserved in {capital_preserved} evidence-threshold decision(s).")

        return LearningState(
            stage=stage,  # type: ignore[arg-type]
            reviewed_decisions=len(decisions),
            approved_previews=approved_previews,
            paper_executions=paper_executions,
            risk_vetoes=risk_vetoes,
            capital_preserved=capital_preserved,
            closed_outcomes=closed_outcomes,
            pending_outcomes=pending_outcomes,
            minimum_outcomes_for_promotion=self.MINIMUM_OUTCOMES,
            candidates=candidates,
            common_vetoes=[
                CommonVeto(gate=gate, count=count)
                for gate, count in veto_counts.most_common(5)
            ],
            lessons=lessons,
        )


def write_daily_tables(
    archive_dir: Path,
    days: list[str],
    decisions: list[DecisionPassport],
    execution_events: list[ExecutionEvent],
) -> None:
    """Write small per-day CSVs (decisions, orders, closed trades) next to each archived day."""

    tables: dict[str, tuple[list[str], list[list[object]]]] = {
        "decisions.csv": (["created_at", "decision_id", "cycle_id", "symbol", "strategy", "status",
                           "confidence", "max_loss", "max_profit", "blocked_by"],
                          [[item.created_at.isoformat(), item.decision_id, item.cycle_id, item.symbol,
                            item.plan.strategy.value, item.status.value, item.plan.confidence,
                            item.plan.max_loss, item.plan.max_profit, ";".join(item.blocked_by)]
                           for item in decisions]),
        "orders.csv": (["recorded_at", "kind", "symbol", "status", "order_id", "decision_id",
                        "filled_qty", "filled_avg_price"],
                       [[item.recorded_at.isoformat(), item.kind, item.symbol, item.status, item.order_id,
                         item.decision_id, item.filled_qty, item.filled_avg_price]
                        for item in execution_events]),
        "trades.csv": (["closed_at", "opened_at", "key", "strategy", "entries", "pnl"],
                       [[trade["closed_at"].isoformat(), trade["opened_at"].isoformat(), trade["key"],
                         trade["strategy"].value, len(trade["decision_ids"]), trade["pnl"]]
                        for trade in closed_trades(decisions, execution_events)]),
    }
    for day in days:
        folder = archive_dir / day
        folder.mkdir(parents=True, exist_ok=True)
        for name, (header, rows) in tables.items():
            with (folder / name).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(row for row in rows if str(row[0]).startswith(day))
