"""Evidence accounting for the champion/challenger learning loop.

The learner never fabricates outcomes. Until closed paper trades exist it only
summarises decision coverage, approvals, vetoes and missing evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from models import (
    CommonVeto,
    DecisionPassport,
    DecisionStatus,
    ExecutionEvent,
    LearningState,
    StrategyEvidence,
    StrategyKind,
)


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
            if item.kind == "order_update" and item.status.lower() == "filled" and item.order_id
        }
        paper_executions = len(filled_entry_orders) + sum(
            item.status == DecisionStatus.EXECUTED and item.receipt.mode == "paper"
            for item in decisions
        )
        risk_vetoes = sum(item.status == DecisionStatus.REJECTED for item in decisions)
        capital_preserved = sum(item.status == DecisionStatus.NO_TRADE for item in decisions)

        closed_outcomes = len(
            {
                item.order_id
                for item in execution_events
                if item.kind == "exit_update" and item.status.lower() == "filled" and item.order_id
            }
        )
        pending_outcomes = max(0, paper_executions - closed_outcomes)
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
                )
            )
        candidates.sort(key=lambda item: (-item.reviewed, -item.average_gate_pass_rate, item.strategy.value))

        lessons = [
            "No strategy can be promoted until at least five verified closed paper outcomes exist.",
        ]
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
