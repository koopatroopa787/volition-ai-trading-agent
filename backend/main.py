"""FastAPI surface for Volition."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import settings
from engine import VolitionEngine


engine = VolitionEngine(settings)
logger = logging.getLogger("volition.scheduler")


async def _scheduler_loop() -> None:
    """Run autonomous paper cycles at a bounded cadence.

    The first scheduled cycle waits for a full interval, giving operators time
    to inspect the deployment or arm the kill switch after a restart.
    """

    interval = settings.agent_interval_minutes * 60
    while True:
        engine.runtime_store.schedule(datetime.now(timezone.utc) + timedelta(seconds=interval))
        await asyncio.sleep(interval)
        try:
            await engine.run_scheduled_cycle()
        except Exception as exc:
            logger.exception("Scheduled review failed safely: %s", type(exc).__name__)
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task: asyncio.Task[None] | None = None
    if settings.volition_mode == "paper" and settings.scheduler_enabled:
        task = asyncio.create_task(_scheduler_loop(), name="volition-paper-scheduler")
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Volition API",
    version="0.1.0",
    description="Autonomous, audit-first options risk desk for Alpaca paper trading.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Volition-Operator-Key"],
)


def require_operator(
    supplied_key: Annotated[str | None, Header(alias="X-Volition-Operator-Key")] = None,
) -> None:
    """Protect state-changing operator routes while keeping demo setup frictionless."""

    if not settings.operator_auth_required:
        return
    if not settings.operator_api_key:
        raise HTTPException(
            status_code=503,
            detail="Operator controls are locked until OPERATOR_API_KEY is configured.",
        )
    if not supplied_key or not secrets.compare_digest(supplied_key, settings.operator_api_key):
        raise HTTPException(
            status_code=401,
            detail="Valid operator access is required for this action.",
        )

class CycleRequest(BaseModel):
    symbol: str | None = Field(default=None, max_length=12)


class StrategyLabRequest(BaseModel):
    symbol: str | None = Field(default=None, max_length=12)
    paths: int = Field(default=5_000, ge=1_000, le=20_000)
    scenario: Literal["base", "volatility_shock", "bullish_tape", "bearish_tape"] = "base"


class KillSwitchRequest(BaseModel):
    armed: bool


@app.get("/health")
def health() -> dict[str, object]:
    valid, records = engine.ledger.verify()
    return {
        "status": "ok",
        "mode": settings.volition_mode,
        "execution_mode": settings.execution_mode,
        "paper_only": settings.alpaca_paper_trade,
        "can_submit_orders": settings.can_submit_orders,
        "operator_controls_configured": (
            not settings.operator_auth_required or bool(settings.operator_api_key)
        ),
        "scheduler_enabled": settings.scheduler_enabled,
        "agent_interval_minutes": settings.agent_interval_minutes,
        "ledger_valid": valid,
        "ledger_records": records,
        "scheduler_last_heartbeat_at": engine.runtime_store.state.last_heartbeat_at,
        "scheduler_last_cycle_status": engine.runtime_store.state.last_cycle_status,
        "scheduler_last_cycle_at": engine.runtime_store.state.last_cycle_at,
        "scheduler_consecutive_failures": engine.runtime_store.state.consecutive_failures,
        "execution_events": len(engine.execution_ledger.recent(limit=100_000)),
    }


@app.get("/api/dashboard")
async def dashboard():
    return await engine.dashboard()


@app.get("/api/market-pulse")
async def market_pulse():
    try:
        return await engine.market_pulse()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market pulse failed: {type(exc).__name__}") from exc


@app.get("/api/intelligence")
async def intelligence():
    return await engine.intelligence()


@app.get("/api/learning")
def learning():
    return engine.learning()


@app.post("/api/strategy-lab")
async def strategy_lab(payload: StrategyLabRequest):
    try:
        return await engine.strategy_lab(payload.symbol, payload.paths, payload.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Simulation failed: {type(exc).__name__}") from exc


@app.post("/api/cycle", dependencies=[Depends(require_operator)])
async def run_cycle(payload: CycleRequest):
    try:
        return await engine.run_cycle(payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # keep credentials and raw payloads out of errors
        raise HTTPException(status_code=502, detail=f"Agent cycle failed: {type(exc).__name__}") from exc


@app.post("/api/kill-switch", dependencies=[Depends(require_operator)])
def kill_switch(payload: KillSwitchRequest) -> dict[str, object]:
    armed = engine.set_kill_switch(payload.armed)
    return {
        "armed": armed,
        "message": "All new orders are blocked." if armed else "Autonomous cycles may trade when every gate passes.",
    }


@app.get("/api/decisions/{decision_id}")
def get_decision(decision_id: str):
    decision = engine.decision(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="Decision passport not found")
    return decision


@app.get("/api/ledger/verify")
def verify_ledger() -> dict[str, object]:
    valid, records = engine.ledger.verify()
    return {"valid": valid, "records": records}
