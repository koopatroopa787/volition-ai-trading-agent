"""Typed contracts shared across the autonomous trading pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrategyKind(str, Enum):
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    IRON_CONDOR = "iron_condor"
    LONG_STRADDLE = "long_straddle"
    LONG_STRANGLE = "long_strangle"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    UNCLASSIFIED = "unclassified"
    NO_TRADE = "no_trade"


class DecisionStatus(str, Enum):
    EXECUTED = "executed"
    SUBMITTED = "submitted"
    PREVIEWED = "previewed"
    REJECTED = "rejected"
    NO_TRADE = "no_trade"
    FAILED = "failed"


class OptionContract(BaseModel):
    symbol: str
    underlying: str
    expiration: str
    strike: float
    option_type: Literal["call", "put"]
    bid: float
    ask: float
    delta: float
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    implied_volatility: float
    open_interest: int = 0
    volume: int = 0

    @computed_field
    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 4)

    @computed_field
    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 100.0
        return round(((self.ask - self.bid) / self.mid) * 100, 2)


class PricePoint(BaseModel):
    timestamp: datetime
    close: float
    volume: float = 0.0


class MarketSnapshot(BaseModel):
    symbol: str
    captured_at: datetime = Field(default_factory=utc_now)
    underlying_price: float
    trend_score: float = Field(ge=-1.0, le=1.0)
    realized_volatility: float = Field(ge=0)
    iv_rank: float = Field(ge=0, le=100)
    catalyst_score: float = Field(default=0.0, ge=0, le=1.0)
    news_sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    catalyst_types: list[str] = Field(default_factory=list)
    regime: str
    market_open: bool = True
    contracts: list[OptionContract] = Field(default_factory=list)
    price_history: list[PricePoint] = Field(default_factory=list)
    price_history_source: str = "Demo-labelled price path"
    source: str = "demo-simulated"


class AccountSnapshot(BaseModel):
    account_id: str
    status: str = "ACTIVE"
    created_at: datetime | None = None
    equity: float
    last_equity: float
    cash: float
    options_buying_power: float
    portfolio_value: float
    options_trading_level: int = 3
    open_positions: int = 0
    open_risk: float = 0.0
    starting_balance: float = 100_000.0
    source: str = "demo-simulated"

    @computed_field
    @property
    def daily_pnl(self) -> float:
        return round(self.equity - self.last_equity, 2)

    @computed_field
    @property
    def daily_pnl_pct(self) -> float:
        if self.last_equity == 0:
            return 0.0
        return round((self.daily_pnl / self.last_equity) * 100, 3)

    @computed_field
    @property
    def competition_pnl(self) -> float:
        return round(self.equity - self.starting_balance, 2)


class TradeLeg(BaseModel):
    symbol: str
    option_type: Literal["call", "put"]
    side: Literal["buy", "sell"]
    position_intent: Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]
    strike: float
    expiration: str
    ratio_qty: int = 1
    delta: float = 0.0
    mid: float = 0.0
    spread_pct: float = 0.0
    open_interest: int = 0


class TradePlan(BaseModel):
    symbol: str
    strategy: StrategyKind
    thesis: str
    confidence: float = Field(ge=0, le=1)
    quantity: int = Field(default=1, ge=0)
    order_type: Literal["market", "limit"] = "limit"
    limit_price: float | None = None
    legs: list[TradeLeg] = Field(default_factory=list)
    max_loss: float = 0.0
    max_profit: float | None = None
    net_delta: float = 0.0
    net_theta: float = 0.0
    dte: int = 0
    no_trade_reason: str | None = None
    alternatives_rejected: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    name: str
    passed: bool
    observed: str
    limit: str
    severity: Literal["info", "warning", "block"] = "block"


class AgentOpinion(BaseModel):
    agent: str
    verdict: Literal["support", "oppose", "abstain"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    evidence: list[str] = Field(default_factory=list)
    model: str = "deterministic-fallback"


class ExecutionReceipt(BaseModel):
    mode: Literal["demo", "preview", "paper"]
    accepted: bool
    order_id: str | None = None
    client_order_id: str | None = None
    raw_status: str = "not_submitted"
    command_evidence: list[str] = Field(default_factory=list)
    message: str = ""


class ExecutionEvent(BaseModel):
    event_id: str
    recorded_at: datetime = Field(default_factory=utc_now)
    decision_id: str | None = None
    cycle_id: str | None = None
    symbol: str
    kind: Literal["review", "order_submitted", "order_update", "exit_submitted", "exit_update"]
    status: str
    order_id: str | None = None
    client_order_id: str | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    message: str


class RuntimeState(BaseModel):
    kill_switch: bool = False
    last_heartbeat_at: datetime | None = None
    last_cycle_at: datetime | None = None
    last_cycle_status: str = "not_started"
    last_cycle_message: str = "Waiting for the first scheduled review."
    next_cycle_at: datetime | None = None
    consecutive_failures: int = 0


class DecisionPassport(BaseModel):
    decision_id: str
    created_at: datetime = Field(default_factory=utc_now)
    cycle_id: str
    symbol: str
    status: DecisionStatus
    plan: TradePlan
    opinions: list[AgentOpinion] = Field(default_factory=list)
    gates: list[GateResult] = Field(default_factory=list)
    receipt: ExecutionReceipt
    evidence: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str = "GENESIS"
    record_hash: str = ""

    @computed_field
    @property
    def blocked_by(self) -> list[str]:
        return [gate.name for gate in self.gates if not gate.passed and gate.severity == "block"]


class PositionView(BaseModel):
    symbol: str
    strategy: StrategyKind
    opened_at: str
    expiration: str
    quantity: int
    cost_basis: float
    market_value: float
    unrealized_pnl: float
    max_loss: float
    risk_used_pct: float
    thesis_health: int = Field(ge=0, le=100)
    legs: list[str]
    raw_legs: list[dict[str, Any]] = Field(default_factory=list)


class MarketBoardItem(BaseModel):
    symbol: str
    price: float
    period_change_pct: float
    trend_score: float = Field(ge=-1.0, le=1.0)
    realized_volatility: float = Field(ge=0.0)
    iv_rank: float = Field(ge=0.0, le=100.0)
    regime: str
    latest_bar_at: datetime | None = None
    source: str
    bars: list[PricePoint] = Field(default_factory=list)


class MarketPulseItem(BaseModel):
    symbol: str
    label: str
    group: str
    proxy_for: str
    price: float = Field(ge=0.0)
    change_pct: float
    change_30d_pct: float
    day_open: float = Field(ge=0.0)
    day_high: float = Field(ge=0.0)
    day_low: float = Field(ge=0.0)
    volume: float = Field(ge=0.0)
    updated_at: datetime | None = None
    source: str
    bars: list[PricePoint] = Field(default_factory=list)


class MarketBreadth(BaseModel):
    advancing: int = Field(ge=0)
    declining: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    average_change_pct: float
    tone: Literal["risk-on", "mixed", "risk-off"]


class MarketPulseState(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    source: str
    refresh_seconds: int = Field(ge=15)
    groups: list[str]
    tracked_assets: int = Field(ge=0)
    strategy_eligible: int = Field(ge=0)
    market_open: bool
    breadth: MarketBreadth
    items: list[MarketPulseItem]


class MarketClockState(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    is_open: bool
    next_open: datetime
    next_close: datetime
    timezone: str = "America/New_York"
    source: str


class CommonVeto(BaseModel):
    gate: str
    count: int


class StrategyEvidence(BaseModel):
    strategy: StrategyKind
    reviewed: int
    approved: int
    vetoed: int
    average_confidence: float = Field(ge=0.0, le=1.0)
    average_gate_pass_rate: float = Field(ge=0.0, le=1.0)
    realized_trades: int = 0
    realized_pnl: float = 0.0
    win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["collecting", "challenger", "champion"] = "collecting"


class LearningState(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    stage: Literal["collecting_evidence", "calibrating", "champion_challenger"]
    reviewed_decisions: int
    approved_previews: int
    paper_executions: int
    risk_vetoes: int
    capital_preserved: int
    closed_outcomes: int
    pending_outcomes: int
    minimum_outcomes_for_promotion: int = 5
    candidates: list[StrategyEvidence] = Field(default_factory=list)
    common_vetoes: list[CommonVeto] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)


class EquityPoint(BaseModel):
    label: str
    equity: float
    benchmark: float


class RequirementCheck(BaseModel):
    label: str
    passed: bool
    detail: str


class AgentStage(BaseModel):
    key: str
    label: str
    status: Literal["idle", "running", "done", "blocked"] = "idle"
    detail: str
    duration_ms: int = 0


class NewsInsight(BaseModel):
    id: str
    symbol: str
    headline: str
    summary: str = ""
    url: str = ""
    source: str
    published_at: datetime | None = None
    sentiment_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    sentiment_label: Literal["positive", "neutral", "negative"] = "neutral"
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    catalyst_type: str = "market"
    provider: str


class FilingEvent(BaseModel):
    symbol: str
    company: str
    form: str
    filed_at: str
    report_date: str = ""
    description: str = ""
    url: str
    material: bool = False
    provider: str = "SEC EDGAR"


class MacroSignal(BaseModel):
    series_id: str
    label: str
    value: float
    previous: float
    change: float
    unit: str
    observed_at: str
    risk_impulse: float = Field(default=0.0, ge=-1.0, le=1.0)
    provider: str = "FRED"


class DataSourceStatus(BaseModel):
    name: str
    configured: bool
    available: bool
    detail: str
    url: str


class IntelligenceState(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    news: list[NewsInsight] = Field(default_factory=list)
    filings: list[FilingEvent] = Field(default_factory=list)
    macro: list[MacroSignal] = Field(default_factory=list)
    aggregate_sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    macro_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[DataSourceStatus] = Field(default_factory=list)


class PercentileBandPoint(BaseModel):
    day: int
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class HistogramBin(BaseModel):
    lower: float
    upper: float
    count: int
    probability: float = Field(ge=0.0, le=1.0)


class OutcomeScenario(BaseModel):
    key: str
    label: str
    condition: str
    probability: float = Field(ge=0.0, le=1.0)
    profitable_at_expiry: bool
    interpretation: str


class ProbabilityCheckpoint(BaseModel):
    day: int
    median_price: float
    probability_profitable_if_expiry: float = Field(ge=0.0, le=1.0)


class MonteCarloResult(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    symbol: str
    strategy: StrategyKind
    plan: TradePlan
    spot_price: float
    expiration: str
    horizon_days: int
    paths: int
    seed: int
    annual_drift: float
    annual_volatility: float
    estimated_slippage: float
    scenario: Literal["base", "volatility_shock", "bullish_tape", "bearish_tape"] = "base"
    scenario_label: str = "Current evidence"
    scenario_description: str = "Current trend, news and volatility evidence."
    proposal_status: Literal["executable", "research_only", "risk_blocked"] = "executable"
    eligibility_reason: str = "Current proposal cleared the strategy evidence threshold."
    expected_pnl: float
    median_pnl: float
    probability_profit: float = Field(ge=0.0, le=1.0)
    probability_near_max_loss: float = Field(ge=0.0, le=1.0)
    value_at_risk_95: float
    expected_shortfall_95: float
    pnl_percentiles: dict[str, float]
    terminal_price_percentiles: dict[str, float]
    break_even_lower: float | None = None
    break_even_upper: float | None = None
    outcome_scenarios: list[OutcomeScenario] = Field(default_factory=list)
    probability_checkpoints: list[ProbabilityCheckpoint] = Field(default_factory=list)
    underlying_bands: list[PercentileBandPoint]
    distribution: list[HistogramBin]
    assumptions: list[str]


class DashboardState(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    mode: Literal["demo", "paper"]
    data_label: str
    account: AccountSnapshot
    market_board: list[MarketBoardItem]
    opportunities: list[TradePlan]
    positions: list[PositionView]
    recent_decisions: list[DecisionPassport]
    equity_curve: list[EquityPoint]
    requirements: list[RequirementCheck]
    agent_stages: list[AgentStage]
    learning: LearningState
    market_clock: MarketClockState
    kill_switch: bool
    autonomous: bool
    order_submission_enabled: bool
    operator_controls_configured: bool
    market_open: bool
    scheduler_state: Literal["running", "waiting_for_market", "paused"]
    next_cycle_at: str
    scheduler_last_heartbeat_at: str | None = None
    scheduler_last_cycle_at: str | None = None
    scheduler_last_cycle_status: str = "not_started"
    scheduler_last_cycle_message: str = "Waiting for the first scheduled review."
    scheduler_consecutive_failures: int = 0
    execution_events: list[ExecutionEvent] = Field(default_factory=list)
    reasoning_mode: Literal["hosted_model", "local_model", "deterministic_fallback"] = "deterministic_fallback"
    reasoning_model: str = "deterministic-fallback"
    watchlist: list[str]
    scan_shortlist: list[str] = Field(default_factory=list)
    cooldown_symbols: list[str] = Field(default_factory=list)
    deep_scan_limit: int = Field(default=5, ge=1)
