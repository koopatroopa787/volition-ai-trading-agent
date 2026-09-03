export type Strategy =
  | "bull_call_spread"
  | "bear_put_spread"
  | "iron_condor"
  | "long_straddle"
  | "long_strangle"
  | "long_call"
  | "long_put"
  | "unclassified"
  | "no_trade";

export type SimulationScenario = "base" | "volatility_shock" | "bullish_tape" | "bearish_tape";

export type MarketPulseGroup = "Indices" | "Commodities" | "Macro & risk" | "Sectors" | "Leaders";

export type MarketPulse = {
  generated_at: string;
  source: string;
  refresh_seconds: number;
  groups: MarketPulseGroup[];
  tracked_assets: number;
  strategy_eligible: number;
  market_open: boolean;
  breadth: {
    advancing: number;
    declining: number;
    unchanged: number;
    average_change_pct: number;
    tone: "risk-on" | "mixed" | "risk-off";
  };
  items: Array<{
    symbol: string;
    label: string;
    group: MarketPulseGroup;
    proxy_for: string;
    price: number;
    change_pct: number;
    change_30d_pct: number;
    day_open: number;
    day_high: number;
    day_low: number;
    volume: number;
    updated_at: string | null;
    source: string;
    bars: Array<{ timestamp: string; close: number; volume: number }>;
  }>;
};

export type Gate = {
  name: string;
  passed: boolean;
  observed: string;
  limit: string;
  severity: "info" | "warning" | "block";
};

export type Leg = {
  symbol: string;
  option_type: "call" | "put";
  side: "buy" | "sell";
  strike: number;
  expiration: string;
  ratio_qty: number;
  delta: number;
  mid: number;
  spread_pct: number;
  open_interest: number;
};

export type TradePlan = {
  symbol: string;
  strategy: Strategy;
  thesis: string;
  confidence: number;
  quantity: number;
  limit_price: number | null;
  legs: Leg[];
  max_loss: number;
  max_profit: number | null;
  net_delta: number;
  net_theta: number;
  dte: number;
  no_trade_reason: string | null;
  alternatives_rejected: string[];
};

export type AgentOpinion = {
  agent: string;
  verdict: "support" | "oppose" | "abstain";
  confidence: number;
  summary: string;
  evidence: string[];
  model: string;
};

export type Passport = {
  decision_id: string;
  created_at: string;
  cycle_id: string;
  symbol: string;
  status: "executed" | "submitted" | "previewed" | "rejected" | "no_trade" | "failed";
  plan: TradePlan;
  opinions: AgentOpinion[];
  gates: Gate[];
  receipt: {
    mode: "demo" | "preview" | "paper";
    accepted: boolean;
    order_id: string | null;
    client_order_id: string | null;
    raw_status: string;
    command_evidence: string[];
    message: string;
  };
  evidence: Record<string, string | number>;
  previous_hash: string;
  record_hash: string;
  blocked_by: string[];
};

export type Dashboard = {
  generated_at: string;
  mode: "demo" | "paper";
  data_label: string;
  account: {
    account_id: string;
    equity: number;
    last_equity: number;
    cash: number;
    options_buying_power: number;
    portfolio_value: number;
    options_trading_level: number;
    open_positions: number;
    open_risk: number;
    starting_balance: number;
    source: string;
    daily_pnl: number;
    daily_pnl_pct: number;
    competition_pnl: number;
  };
  market_board: Array<{
    symbol: string;
    price: number;
    period_change_pct: number;
    trend_score: number;
    realized_volatility: number;
    iv_rank: number;
    regime: string;
    latest_bar_at: string | null;
    source: string;
    bars: Array<{ timestamp: string; close: number; volume: number }>;
  }>;
  opportunities: TradePlan[];
  positions: Array<{
    symbol: string;
    strategy: Strategy;
    opened_at: string;
    expiration: string;
    quantity: number;
    cost_basis: number;
    market_value: number;
    unrealized_pnl: number;
    max_loss: number;
    risk_used_pct: number;
    thesis_health: number;
    legs: string[];
  }>;
  recent_decisions: Passport[];
  equity_curve: Array<{ label: string; equity: number; benchmark: number }>;
  requirements: Array<{ label: string; passed: boolean; detail: string }>;
  agent_stages: Array<{ key: string; label: string; status: string; detail: string; duration_ms: number }>;
  learning: {
    generated_at: string;
    stage: "collecting_evidence" | "calibrating" | "champion_challenger";
    reviewed_decisions: number;
    approved_previews: number;
    paper_executions: number;
    risk_vetoes: number;
    capital_preserved: number;
    closed_outcomes: number;
    pending_outcomes: number;
    minimum_outcomes_for_promotion: number;
    candidates: Array<{
      strategy: Strategy;
      reviewed: number;
      approved: number;
      vetoed: number;
      average_confidence: number;
      average_gate_pass_rate: number;
      realized_trades: number;
      realized_pnl: number;
      win_rate: number | null;
      status: "collecting" | "challenger" | "champion";
    }>;
    common_vetoes: Array<{ gate: string; count: number }>;
    lessons: string[];
  };
  market_clock: {
    timestamp: string;
    is_open: boolean;
    next_open: string;
    next_close: string;
    timezone: string;
    source: string;
  };
  kill_switch: boolean;
  autonomous: boolean;
  order_submission_enabled: boolean;
  operator_controls_configured: boolean;
  market_open: boolean;
  scheduler_state: "running" | "waiting_for_market" | "paused";
  next_cycle_at: string;
  scheduler_last_heartbeat_at: string | null;
  scheduler_last_cycle_at: string | null;
  scheduler_last_cycle_status: string;
  scheduler_last_cycle_message: string;
  scheduler_consecutive_failures: number;
  execution_events: Array<{
    event_id: string;
    recorded_at: string;
    decision_id: string | null;
    cycle_id: string | null;
    symbol: string;
    kind: "review" | "order_submitted" | "order_update" | "exit_submitted" | "exit_update";
    status: string;
    order_id: string | null;
    client_order_id: string | null;
    filled_qty: number;
    filled_avg_price: number | null;
    message: string;
  }>;
  reasoning_mode: "hosted_model" | "local_model" | "deterministic_fallback";
  reasoning_model: string;
  watchlist: string[];
  scan_shortlist: string[];
  cooldown_symbols: string[];
  deep_scan_limit: number;
};

export type Intelligence = {
  generated_at: string;
  aggregate_sentiment: number;
  macro_risk_score: number;
  news: Array<{
    id: string;
    symbol: string;
    headline: string;
    summary: string;
    url: string;
    source: string;
    published_at: string | null;
    sentiment_score: number;
    sentiment_label: "positive" | "neutral" | "negative";
    relevance: number;
    catalyst_type: string;
    provider: string;
  }>;
  filings: Array<{
    symbol: string;
    company: string;
    form: string;
    filed_at: string;
    report_date: string;
    description: string;
    url: string;
    material: boolean;
    provider: string;
  }>;
  macro: Array<{
    series_id: string;
    label: string;
    value: number;
    previous: number;
    change: number;
    unit: string;
    observed_at: string;
    risk_impulse: number;
    provider: string;
  }>;
  sources: Array<{
    name: string;
    configured: boolean;
    available: boolean;
    detail: string;
    url: string;
  }>;
};

export type MonteCarloResult = {
  generated_at: string;
  symbol: string;
  strategy: Strategy;
  plan: TradePlan;
  spot_price: number;
  expiration: string;
  horizon_days: number;
  paths: number;
  seed: number;
  annual_drift: number;
  annual_volatility: number;
  estimated_slippage: number;
  scenario: SimulationScenario;
  scenario_label: string;
  scenario_description: string;
  proposal_status: "executable" | "research_only" | "risk_blocked";
  eligibility_reason: string;
  expected_pnl: number;
  median_pnl: number;
  probability_profit: number;
  probability_near_max_loss: number;
  value_at_risk_95: number;
  expected_shortfall_95: number;
  pnl_percentiles: Record<string, number>;
  terminal_price_percentiles: Record<string, number>;
  break_even_lower: number | null;
  break_even_upper: number | null;
  outcome_scenarios: Array<{
    key: string;
    label: string;
    condition: string;
    probability: number;
    profitable_at_expiry: boolean;
    interpretation: string;
  }>;
  probability_checkpoints: Array<{
    day: number;
    median_price: number;
    probability_profitable_if_expiry: number;
  }>;
  underlying_bands: Array<{
    day: number;
    p10: number;
    p25: number;
    p50: number;
    p75: number;
    p90: number;
  }>;
  distribution: Array<{
    lower: number;
    upper: number;
    count: number;
    probability: number;
  }>;
  assumptions: string[];
};
