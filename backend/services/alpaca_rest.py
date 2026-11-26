"""Alpaca paper Trading API execution gateway."""

from __future__ import annotations

import uuid

import httpx

from config import Settings
from models import ExecutionReceipt, PositionView, StrategyKind, TradePlan


class AlpacaPaperExecutor:
    BASE_URL = "https://paper-api.alpaca.markets"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def signed_limit_price(plan: TradePlan) -> float | None:
        """Alpaca encodes multi-leg credits as negative limit prices."""

        if plan.limit_price is None:
            return None
        magnitude = abs(plan.limit_price)
        return -magnitude if plan.strategy == StrategyKind.IRON_CONDOR else magnitude

    @classmethod
    def order_payload(cls, plan: TradePlan, client_order_id: str) -> dict[str, object]:
        payload: dict[str, object | None] = {
            "order_class": "mleg",
            "qty": str(plan.quantity),
            "type": plan.order_type,
            "limit_price": (
                str(cls.signed_limit_price(plan))
                if cls.signed_limit_price(plan) is not None
                else None
            ),
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "ratio_qty": str(leg.ratio_qty),
                    "side": leg.side,
                    "position_intent": leg.position_intent,
                }
                for leg in plan.legs
            ],
        }
        return {key: value for key, value in payload.items() if value is not None}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def execute(
        self, plan: TradePlan, cycle_id: str, *, client_order_id: str | None = None
    ) -> ExecutionReceipt:
        evidence = [
            "Alpaca CLI · account get · structured JSON",
            f"Alpaca CLI · data option chain --underlying-symbol {plan.symbol} · Greeks + IV",
        ]
        if plan.strategy == StrategyKind.NO_TRADE:
            return ExecutionReceipt(
                mode="demo" if self.settings.volition_mode == "demo" else "preview",
                accepted=False,
                raw_status="no_trade",
                command_evidence=evidence,
                message=plan.no_trade_reason or "The agent deliberately preserved cash.",
            )

        if self.settings.volition_mode == "demo":
            return ExecutionReceipt(
                mode="demo",
                accepted=True,
                order_id=f"demo-{uuid.uuid4().hex[:10]}",
                client_order_id=f"volition-demo-{cycle_id[-8:]}",
                raw_status="simulated_fill",
                command_evidence=[*evidence, "Demo execution · explicitly simulated, never submitted"],
                message="Simulated fill for the credential-free product walkthrough.",
            )

        if not self.settings.can_submit_orders:
            return ExecutionReceipt(
                mode="preview",
                accepted=True,
                client_order_id=f"volition-preview-{cycle_id[-8:]}",
                raw_status="risk_approved_not_submitted",
                command_evidence=[*evidence, "Trading API · POST /v2/orders · submission disabled"],
                message=(
                    "All gates passed, but submission remains disabled. Set EXECUTION_MODE=paper and "
                    "ALLOW_ORDER_SUBMISSION=true for the dedicated hackathon paper account."
                ),
            )

        client_order_id = client_order_id or f"volition-{cycle_id[-12:]}-{uuid.uuid4().hex[:6]}"
        payload = self.order_payload(plan, client_order_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.BASE_URL}/v2/orders", headers=self._headers, json=payload)
                response.raise_for_status()
                body = response.json()
            return ExecutionReceipt(
                mode="paper",
                accepted=True,
                order_id=body.get("id"),
                client_order_id=body.get("client_order_id", client_order_id),
                raw_status=body.get("status", "accepted"),
                command_evidence=[*evidence, "Alpaca Trading API · POST /v2/orders · atomic mleg"],
                message="Atomic multi-leg order accepted by the Alpaca paper Trading API.",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {408, 409, 429} or exc.response.status_code >= 500:
                return await self._recover_uncertain_submission(client_order_id, evidence, "entry")
            detail = exc.response.text[:500]
            return ExecutionReceipt(
                mode="paper",
                accepted=False,
                client_order_id=client_order_id,
                raw_status=f"http_{exc.response.status_code}",
                command_evidence=[*evidence, "Alpaca Trading API · POST /v2/orders · rejected"],
                message=f"Alpaca rejected the paper order: {detail}",
            )
        except Exception:  # noqa: BLE001
            return await self._recover_uncertain_submission(client_order_id, evidence, "entry")

    async def order_by_client_order_id(self, client_order_id: str) -> dict[str, object]:
        """Resolve an order even when the submission response never arrived."""

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v2/orders:by_client_order_id",
                headers=self._headers,
                params={"client_order_id": client_order_id},
            )
            response.raise_for_status()
            body = response.json()
        return body if isinstance(body, dict) else {}

    async def _recover_uncertain_submission(
        self, client_order_id: str, evidence: list[str], kind: str
    ) -> ExecutionReceipt:
        try:
            body = await self.order_by_client_order_id(client_order_id)
            if body.get("id"):
                return ExecutionReceipt(
                    mode="paper",
                    accepted=str(body.get("status", "")).lower() not in {"rejected", "canceled", "expired"},
                    order_id=str(body["id"]),
                    client_order_id=str(body.get("client_order_id") or client_order_id),
                    raw_status=str(body.get("status") or "accepted"),
                    command_evidence=[*evidence, "Alpaca Trading API · recovered by client order ID"],
                    message=f"Alpaca confirmed the paper {kind} after an uncertain submission response.",
                )
        except Exception:  # A 404 may be transient; absence is not proof of rejection.
            pass
        return ExecutionReceipt(
            mode="paper",
            accepted=False,
            client_order_id=client_order_id,
            raw_status="submission_unknown",
            command_evidence=[*evidence, "Alpaca Trading API · outcome needs reconciliation"],
            message=(
                f"The paper {kind} outcome is unknown. Do not submit another order for this "
                "position until Alpaca confirms this client order ID."
            ),
        )

    async def order(self, order_id: str) -> dict[str, object]:
        """Fetch the broker's current lifecycle state for a submitted order."""

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.BASE_URL}/v2/orders/{order_id}",
                headers=self._headers,
                params={"nested": "true"},
            )
            response.raise_for_status()
            body = response.json()
        return body if isinstance(body, dict) else {}

    async def close_position(
        self, position: PositionView, cycle_id: str, reason: str, *, client_order_id: str | None = None
    ) -> ExecutionReceipt:
        """Submit one atomic closing order for a synced defined-risk structure."""

        evidence = [
            "Alpaca Trading API · GET /v2/positions · broker-synced structure",
            f"Lifecycle policy · {reason}",
        ]
        if not self.settings.can_submit_orders:
            return ExecutionReceipt(
                mode="preview",
                accepted=False,
                raw_status="exit_not_submitted",
                command_evidence=evidence,
                message="The exit condition was detected, but paper-order submission is locked.",
            )
        if not position.raw_legs:
            return ExecutionReceipt(
                mode="paper",
                accepted=False,
                raw_status="exit_structure_unavailable",
                command_evidence=evidence,
                message="The broker position did not include enough leg evidence for an atomic close.",
            )

        client_order_id = client_order_id or f"volition-exit-{cycle_id[-10:]}-{uuid.uuid4().hex[:5]}"
        net_debit = 0.0
        legs = []
        for leg in position.raw_legs:
            original_side = str(leg.get("side", "long"))
            side = "sell" if original_side == "long" else "buy"
            midpoint = float(leg.get("mid", 0.0) or 0.0)
            ratio = int(leg.get("ratio_qty", 1) or 1)
            net_debit += midpoint * ratio * (1 if side == "buy" else -1)
            legs.append(
                {
                    "symbol": str(leg["symbol"]),
                    "ratio_qty": str(ratio),
                    "side": side,
                    "position_intent": "sell_to_close" if original_side == "long" else "buy_to_close",
                }
            )
        payload = {
            "order_class": "mleg",
            "qty": str(position.quantity),
            "type": "limit",
            "limit_price": str(round(net_debit, 2)),
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "legs": legs,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.BASE_URL}/v2/orders",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            return ExecutionReceipt(
                mode="paper",
                accepted=True,
                order_id=body.get("id"),
                client_order_id=body.get("client_order_id", client_order_id),
                raw_status=body.get("status", "accepted"),
                command_evidence=[*evidence, "Alpaca Trading API · POST /v2/orders · atomic mleg close"],
                message=f"Atomic paper exit accepted: {reason}.",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {408, 409, 429} or exc.response.status_code >= 500:
                return await self._recover_uncertain_submission(client_order_id, evidence, "exit")
            return ExecutionReceipt(
                mode="paper",
                accepted=False,
                client_order_id=client_order_id,
                raw_status=f"http_{exc.response.status_code}",
                command_evidence=[*evidence, "Alpaca Trading API · exit rejected"],
                message=f"Alpaca rejected the paper exit: {exc.response.text[:500]}",
            )
        except Exception:  # noqa: BLE001
            return await self._recover_uncertain_submission(client_order_id, evidence, "exit")
