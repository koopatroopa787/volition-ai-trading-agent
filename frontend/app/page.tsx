"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dashboard, Intelligence, MarketPulse, MarketPulseGroup, MonteCarloResult, Passport, SimulationScenario, Strategy, TradePlan } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "";
type View = "overview" | "markets" | "lab" | "intelligence" | "journal";

const money = (value: number, digits = 0) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits }).format(value);
const percentage = (value: number, digits = 1, signed = false) => `${signed && value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const compactNumber = (value: number) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
const shortDate = (value: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
const timeAgo = (value: string | null) => {
  if (!value) return "Time unavailable";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 60) return `${minutes || 1} min ago`;
  if (minutes < 1_440) return `${Math.round(minutes / 60)} hr ago`;
  return `${Math.round(minutes / 1_440)} d ago`;
};
const strategyName = (strategy: Strategy) => ({ bull_call_spread: "Bull call spread", bear_put_spread: "Bear put spread", iron_condor: "Iron condor", long_straddle: "Long straddle", long_strangle: "Long strangle", long_call: "Long call", long_put: "Long put", unclassified: "Unclassified structure", no_trade: "Hold cash" })[strategy];
const scenarioOptions: Array<{ key: SimulationScenario; label: string; detail: string }> = [
  { key: "base", label: "Current evidence", detail: "Desk assumptions" },
  { key: "volatility_shock", label: "Volatility shock", detail: "+35% volatility" },
  { key: "bullish_tape", label: "Bullish tape", detail: "+12pt drift" },
  { key: "bearish_tape", label: "Bearish tape", detail: "−12pt drift" },
];

const marketTime = (value: string) => new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
  timeZoneName: "short",
}).format(new Date(value));

const countdown = (milliseconds: number) => {
  const total = Math.max(0, Math.floor(milliseconds / 1_000));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const seconds = total % 60;
  return `${days ? `${days}d ` : ""}${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
};

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="18" height="7" rx="1" /></>,
    markets: <><path d="M3 18 8 12l4 3 8-10" /><path d="M14 5h6v6M3 21h18" /></>,
    lab: <><path d="M9 3h6M10 3v5l-5 9a2.2 2.2 0 0 0 2 3h10a2.2 2.2 0 0 0 2-3l-5-9V3" /><path d="M7.5 14h9" /></>,
    intelligence: <><circle cx="12" cy="12" r="9" /><path d="M8 15h8M9 11h6M11 7h2" /></>,
    journal: <><path d="M5 3h11l3 3v15H5z" /><path d="M15 3v4h4M8 11h8M8 15h8" /></>,
    refresh: <><path d="M20 7v5h-5" /><path d="M4 17v-5h5" /><path d="M6.1 8a7 7 0 0 1 11.7-2L20 8M4 16l2.2 2a7 7 0 0 0 11.7-2" /></>,
    pause: <><path d="M9 5v14M15 5v14" /></>,
    play: <path d="m8 5 11 7-11 7V5Z" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="M12 3 2.8 20h18.4L12 3Z" /><path d="M12 9v5M12 17.5v.1" /></>,
    external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v7H4V6h7" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function PageIntro({ eyebrow, title, copy, side }: { eyebrow: string; title: string; copy: string; side?: React.ReactNode }) {
  return <div className="page-intro"><div><p className="overline">{eyebrow}</p><h1>{title}</h1><p className="page-copy">{copy}</p></div>{side && <div className="intro-side">{side}</div>}</div>;
}

function SessionCountdown({ data, schedulerLabel }: { data: Dashboard; schedulerLabel: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  const target = data.market_clock.is_open ? data.market_clock.next_close : data.market_clock.next_open;
  const action = data.market_clock.is_open ? "Closes" : "Opens";
  const remaining = new Date(target).getTime() - now;
  return <div className="session-clock" role="timer" aria-label={`US market ${action.toLowerCase()} countdown`}><span className={data.market_clock.is_open ? "live-dot" : "closed-dot"} /><div><b>{remaining > 0 ? `${action} in ${countdown(remaining)}` : "Refreshing market session"}</b><small>{marketTime(target)} · {schedulerLabel}</small></div></div>;
}

function SummaryStrip({ data }: { data: Dashboard }) {
  const openRisk = data.account.equity ? (data.account.open_risk / data.account.equity) * 100 : 0;
  const items = [
    ["Portfolio value", money(data.account.portfolio_value, 2), `${money(data.account.cash)} held in cash`],
    ["Today", money(data.account.daily_pnl, 2), `${percentage(data.account.daily_pnl_pct, 2, true)} since prior close`],
    ["Options buying power", money(data.account.options_buying_power), `Approval level ${data.account.options_trading_level}`],
    ["Capital at risk", percentage(openRisk, 2), `${money(data.account.open_risk)} defined maximum loss`],
  ];
  return <section className="summary-strip" aria-label="Account summary">{items.map(([label, value, detail], index) => <div key={label}><span>{label}</span><strong className={index === 1 ? (data.account.daily_pnl >= 0 ? "gain" : "loss") : ""}>{value}</strong><small>{detail}</small></div>)}</section>;
}

function OperationsStrip({ data }: { data: Dashboard }) {
  const nextCycle = data.next_cycle_at.includes("T")
    ? `Next ${new Date(data.next_cycle_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : data.next_cycle_at;
  const cycleStatus = data.scheduler_last_cycle_status || "not_started";
  const reasoningLabel = data.reasoning_mode === "hosted_model" ? "Hosted model" : data.reasoning_mode === "local_model" ? "Private model" : "Rule fallback";
  return <section className="operations-strip" aria-label="Autonomous desk status"><div><span>Order authority</span><strong className={data.order_submission_enabled ? "gain" : "warning-text"}>{data.order_submission_enabled ? "Paper enabled" : "Locked"}</strong><small>{data.order_submission_enabled ? "Alpaca paper only" : "Reviews cannot place orders"}</small></div><div><span>Scheduler heartbeat</span><strong>{data.scheduler_last_heartbeat_at ? timeAgo(data.scheduler_last_heartbeat_at) : "Starting"}</strong><small>{nextCycle}</small></div><div><span>Last autonomous check</span><strong>{cycleStatus.replaceAll("_", " ")}</strong><small>{data.scheduler_last_cycle_message || "Waiting for the first scheduled review."}</small></div><div><span>Reasoning source</span><strong>{reasoningLabel}</strong><small>{data.reasoning_model || "deterministic-fallback"}</small></div></section>;
}

function PerformancePanel({ data }: { data: Dashboard }) {
  const points = data.equity_curve || [];
  const startEquity = points[0]?.equity || data.account.starting_balance;
  const endEquity = points.at(-1)?.equity || data.account.equity;
  const startBenchmark = points[0]?.benchmark || data.account.starting_balance;
  const endBenchmark = points.at(-1)?.benchmark || startBenchmark;
  const returnPct = startEquity ? ((endEquity / startEquity) - 1) * 100 : 0;
  const benchmarkPct = startBenchmark ? ((endBenchmark / startBenchmark) - 1) * 100 : 0;
  let peak = points[0]?.equity || data.account.starting_balance;
  let maxDrawdown = 0;
  points.forEach((point) => {
    peak = Math.max(peak, point.equity);
    maxDrawdown = Math.min(maxDrawdown, peak ? ((point.equity / peak) - 1) * 100 : 0);
  });
  const width = 840, height = 250, left = 58, top = 22, right = 20, bottom = 38;
  const values = points.flatMap((point) => [point.equity, point.benchmark]);
  const low = values.length ? Math.min(...values) : data.account.starting_balance;
  const high = values.length ? Math.max(...values) : data.account.starting_balance;
  const pad = Math.max((high - low) * 0.12, 50);
  const yMin = low - pad, yMax = high + pad;
  const x = (index: number) => left + (index / Math.max(points.length - 1, 1)) * (width - left - right);
  const y = (value: number) => top + ((yMax - value) / Math.max(yMax - yMin, 1)) * (height - top - bottom);
  const line = (key: "equity" | "benchmark") => points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point[key]).toFixed(1)}`).join(" ");
  const ticks = [yMin, (yMin + yMax) / 2, yMax];
  return <section className="surface performance-surface"><div className="section-heading"><div><h2>Competition performance</h2><p>Broker-recorded account equity compared with a date-aligned SPY investment.</p></div><span>{points.length ? `${points.length} daily observations` : "Awaiting broker history"}</span></div><div className="performance-layout"><div className="performance-kpis"><div><span>Since start</span><strong className={returnPct >= 0 ? "gain" : "loss"}>{percentage(returnPct, 2, true)}</strong><small>{money(endEquity - startEquity, 2)} net P&amp;L</small></div><div><span>Versus SPY</span><strong className={returnPct - benchmarkPct >= 0 ? "gain" : "loss"}>{percentage(returnPct - benchmarkPct, 2, true)}</strong><small>SPY {percentage(benchmarkPct, 2, true)}</small></div><div><span>Maximum drawdown</span><strong className={maxDrawdown < 0 ? "loss" : ""}>{percentage(maxDrawdown, 2)}</strong><small>Peak-to-trough equity</small></div><div><span>Evidence source</span><strong>Alpaca</strong><small>Portfolio history + IEX SPY bars</small></div></div>{points.length >= 2 ? <div className="performance-chart"><div className="performance-key"><span><i />Volition</span><span><i />SPY benchmark</span></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Volition competition return ${percentage(returnPct, 2, true)} versus SPY ${percentage(benchmarkPct, 2, true)}`}>{ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} /><text x={left - 10} y={y(tick) + 4} textAnchor="end">{money(tick)}</text></g>)}<path d={line("benchmark")} className="benchmark-line" /><path d={line("equity")} className="equity-line" />{points.map((point, index) => <circle key={point.label} cx={x(index)} cy={y(point.equity)} r="3"><title>{shortDate(point.label)} · Volition {money(point.equity, 2)} · SPY {money(point.benchmark, 2)}</title></circle>)}<text x={left} y={height - 10}>{shortDate(points[0].label)}</text><text x={width - right} y={height - 10} textAnchor="end">{shortDate(points.at(-1)?.label || points[0].label)}</text></svg></div> : <div className="performance-empty"><b>Performance history is beginning now.</b><span>The chart appears after Alpaca records at least two daily equity observations. Current balance is shown without inventing a trend.</span></div>}</div></section>;
}

function ExecutionTimeline({ data }: { data: Dashboard }) {
  const events = (data.execution_events || []).slice(0, 8);
  return <section className="surface lifecycle-surface"><div className="section-heading"><div><h2>Autonomous activity</h2><p>Scheduler reviews and broker order changes—not browser refreshes.</p></div><span>{events.length ? `${events.length} recent events` : "No events yet"}</span></div>{events.length ? <div className="lifecycle-list">{events.map((event) => <div key={event.event_id}><span className={`lifecycle-mark ${event.status}`} /><div><b>{event.symbol} · {event.kind.replaceAll("_", " ")}</b><small>{event.message}</small></div><strong>{event.status.replaceAll("_", " ")}</strong><time>{timeAgo(event.recorded_at)}</time></div>)}</div> : <div className="lifecycle-empty"><b>The scheduler is observable now.</b><span>Its first review, preview, submission, fill, cancellation or managed exit will appear here with broker status.</span></div>}</section>;
}

function PriceSparkline({ market }: { market: Dashboard["market_board"][number] }) {
  const points = market.bars;
  if (points.length < 2) return <div className="spark-empty">Price history unavailable</div>;
  const width = 300, height = 84, pad = 3;
  const closes = points.map((point) => point.close);
  const low = Math.min(...closes), high = Math.max(...closes);
  const x = (index: number) => pad + (index / (points.length - 1)) * (width - pad * 2);
  const y = (value: number) => pad + ((high - value) / Math.max(high - low, .01)) * (height - pad * 2);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.close).toFixed(1)}`).join(" ");
  const lastX = x(points.length - 1), lastY = y(points.at(-1)?.close || market.price);
  return <svg className="market-spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${market.symbol} recent price path from ${money(low, 2)} to ${money(high, 2)}`}><line x1="0" x2={width} y1={height / 2} y2={height / 2} className="spark-guide" /><path d={path} className="spark-line" /><circle cx={lastX} cy={lastY} r="3.2" className="spark-point" /></svg>;
}

function ScanCoverage({ data }: { data: Dashboard }) {
  const shortlist = data.scan_shortlist || data.market_board.map((market) => market.symbol);
  const cooling = data.cooldown_symbols || [];
  return <section className="scan-coverage" aria-label="Trading universe coverage"><div><span>Trading universe</span><strong>{data.watchlist.length}</strong><small>active Alpaca symbols</small></div><div><span>Deep analysis now</span><strong>{shortlist.length}</strong><small>option chains and committee evidence</small></div><div className="scan-symbols"><span>Current shortlist</span><p>{shortlist.map((symbol) => <b key={symbol}>{symbol}</b>)}</p><small>reranked before each cycle</small></div><div><span>Cooling down</span><strong>{cooling.length}</strong><small>reviewed within the last hour</small></div></section>;
}

function MarketBoard({ data }: { data: Dashboard }) {
  const markets = data.market_board;
  const visible = markets.slice(0, 4);
  const latest = visible.map((market) => market.latest_bar_at).filter(Boolean).sort().at(-1) || null;
  return <section className="market-board"><div className="market-board-heading"><div><h2>Deep-analysis board</h2><p>{data.scan_shortlist.length} shortlisted from {data.watchlist.length} markets · independent price scales</p></div><span>{latest ? `Latest bar ${timeAgo(latest)}` : "Awaiting price bars"}</span></div><div className="market-grid">{visible.map((market) => <article key={market.symbol} className="market-card"><div className="market-card-top"><div><b>{market.symbol}</b><span>{market.regime}</span></div><div><strong>{money(market.price, 2)}</strong><small className={market.period_change_pct >= 0 ? "move-up" : "move-down"}>{percentage(market.period_change_pct, 2, true)} recent</small></div></div><PriceSparkline market={market} /><footer><span>IV rank {market.iv_rank.toFixed(0)}</span><span>Realised vol {percentage(market.realized_volatility * 100, 0)}</span><span>{market.source}</span></footer></article>)}</div></section>;
}

function PulseSparkline({ item }: { item: MarketPulse["items"][number] }) {
  const points = item.bars;
  if (points.length < 2) return <div className="pulse-spark-empty">Awaiting history</div>;
  const width = 240, height = 64, pad = 3;
  const closes = points.map((point) => point.close);
  const low = Math.min(...closes), high = Math.max(...closes);
  const path = points.map((point, index) => {
    const x = pad + (index / (points.length - 1)) * (width - pad * 2);
    const y = pad + ((high - point.close) / Math.max(high - low, 0.01)) * (height - pad * 2);
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return <svg className={`pulse-spark ${item.change_30d_pct >= 0 ? "positive" : "negative"}`} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${item.label} 30-session path, ${percentage(item.change_30d_pct, 1, true)}`}><line x1="0" x2={width} y1={height / 2} y2={height / 2} /><path d={path} /></svg>;
}

function MarketTape({ pulse, onOpen }: { pulse: MarketPulse | null; onOpen: () => void }) {
  if (!pulse) return <div className="market-tape market-tape-loading"><span />Loading broad market pulse…</div>;
  const priority = ["SPY", "QQQ", "IWM", "GLD", "USO", "TLT", "UUP", "VIXY", "XLK", "NVDA"];
  const items = priority.map((symbol) => pulse.items.find((item) => item.symbol === symbol)).filter((item): item is MarketPulse["items"][number] => Boolean(item));
  return <div className="market-tape"><button className="tape-label" onClick={onOpen}><span className={pulse.market_open ? "live-dot" : "closed-dot"} /><b>Market pulse</b><small>{pulse.tracked_assets} tracked</small></button><div className="tape-scroll">{items.map((item) => <button key={item.symbol} onClick={onOpen} aria-label={`Open market pulse for ${item.label}`}><b>{item.symbol}</b><span>{money(item.price, item.price < 20 ? 2 : 1)}</span><em className={item.change_pct >= 0 ? "move-up" : "move-down"}>{percentage(item.change_pct, 2, true)}</em></button>)}</div><button className="tape-open" onClick={onOpen}>View all <Icon name="arrow" size={14} /></button></div>;
}

function MarketPulseView({ pulse, error }: { pulse: MarketPulse | null; error: string }) {
  const [group, setGroup] = useState<"All" | MarketPulseGroup>("All");
  if (!pulse) return <><PageIntro eyebrow="Market pulse" title="A broader read of the tape" copy="Loading indices, commodities, rates, sectors and market leaders from a separate lightweight feed." />{error ? <div className="notice error-notice"><Icon name="alert" />{error}</div> : <div className="lab-loading"><span /><h2>Building the cross-asset view</h2><p>Collecting batch snapshots and 30-session context.</p></div>}</>;
  const visible = group === "All" ? pulse.items : pulse.items.filter((item) => item.group === group);
  const groupStats = pulse.groups.map((name) => {
    const items = pulse.items.filter((item) => item.group === name);
    const average = items.length ? items.reduce((sum, item) => sum + item.change_pct, 0) / items.length : 0;
    return { name, average, count: items.length };
  });
  const leader = [...groupStats].sort((a, b) => b.average - a.average)[0];
  const laggard = [...groupStats].sort((a, b) => a.average - b.average)[0];
  const breadthTotal = Math.max(pulse.tracked_assets, 1);
  return <>
    <PageIntro eyebrow="Market pulse" title="The market in one screen" copy="Broad benchmarks, commodity proxies, rates, sectors and liquid leaders refresh independently from the options engine. Use the map to understand context—not as a price forecast." side={<div className={`pulse-tone ${pulse.breadth.tone}`}><span>Cross-asset tone</span><strong>{pulse.breadth.tone.replace("-", " ")}</strong><small>{pulse.breadth.advancing} rising · {pulse.breadth.declining} falling</small></div>} />
    {error && <div className="notice error-notice"><Icon name="alert" />{error}</div>}
    <section className="pulse-summary" aria-label="Market breadth summary">
      <div className="breadth-card"><span>Market breadth</span><strong>{pulse.breadth.advancing} / {pulse.tracked_assets}</strong><small>tracked instruments higher today</small><div className="breadth-track"><i style={{ width: `${pulse.breadth.advancing / breadthTotal * 100}%` }} /><b style={{ width: `${pulse.breadth.unchanged / breadthTotal * 100}%` }} /></div></div>
      <div><span>Average move</span><strong className={pulse.breadth.average_change_pct >= 0 ? "gain" : "loss"}>{percentage(pulse.breadth.average_change_pct, 2, true)}</strong><small>equal-weighted tracker reading</small></div>
      <div><span>Leading group</span><strong>{leader?.name || "—"}</strong><small>{leader ? `${percentage(leader.average, 2, true)} average` : "Awaiting data"}</small></div>
      <div><span>Lagging group</span><strong>{laggard?.name || "—"}</strong><small>{laggard ? `${percentage(laggard.average, 2, true)} average` : "Awaiting data"}</small></div>
    </section>
    <section className="pulse-groups" aria-label="Market group filters"><button className={group === "All" ? "active" : ""} onClick={() => setGroup("All")}><span>All markets</span><b>{pulse.tracked_assets}</b><small>{pulse.strategy_eligible} strategy-eligible</small></button>{groupStats.map((item) => <button key={item.name} className={group === item.name ? "active" : ""} onClick={() => setGroup(item.name)}><span>{item.name}</span><b className={item.average >= 0 ? "gain" : "loss"}>{percentage(item.average, 2, true)}</b><small>{item.count} instruments</small></button>)}</section>
    <div className="pulse-list-heading"><div><h2>{group === "All" ? "Every tracked market" : group}</h2><p>Day move and 30-session price path · each chart uses its own price scale</p></div><span>{pulse.market_open ? `Live session · refreshes every ${pulse.refresh_seconds}s` : `Market closed · last trade snapshots`}<br />{pulse.source} · {timeAgo(pulse.generated_at)}</span></div>
    <section className="pulse-grid">{visible.map((item) => {
      const range = Math.max(item.day_high - item.day_low, 0.01);
      const rangePosition = Math.max(0, Math.min(100, ((item.price - item.day_low) / range) * 100));
      return <article className="pulse-card" key={item.symbol}><header><div><span>{item.group}</span><h3>{item.symbol}<small>{item.label}</small></h3></div><div><strong>{money(item.price, item.price < 20 ? 2 : 1)}</strong><em className={item.change_pct >= 0 ? "move-up" : "move-down"}>{percentage(item.change_pct, 2, true)} today</em></div></header><PulseSparkline item={item} /><div className="pulse-range"><span>Low {money(item.day_low, 1)}</span><i><b style={{ left: `${rangePosition}%` }} /></i><span>High {money(item.day_high, 1)}</span></div><footer><span>{percentage(item.change_30d_pct, 1, true)} over 30 sessions</span><span>{compactNumber(item.volume)} vol.</span><small>{item.proxy_for}</small></footer></article>;
    })}</section>
    <p className="pulse-disclosure">Index and commodity rows are liquid, exchange-traded proxies—not cash-index levels or direct futures quotes. This view measures current market context; Strategy Lab handles scenario probabilities separately.</p>
  </>;
}

function PositionMonitor({ data }: { data: Dashboard }) {
  return <section className="surface position-surface"><div className="section-heading"><div><h2>Position monitor</h2><p>Synced option structures and the lifecycle rules that will govern their exits.</p></div><span>{data.positions.length ? `${data.positions.length} open` : "No open risk"}</span></div>{data.positions.length ? <div className="position-table">{data.positions.map((position) => <div key={`${position.symbol}-${position.expiration}`}><div><strong>{position.symbol}</strong><span>{strategyName(position.strategy)}</span></div><div><strong>{position.legs.join(" · ")}</strong><span>Expires {shortDate(position.expiration)}</span></div><div><strong className={position.unrealized_pnl >= 0 ? "gain" : "loss"}>{money(position.unrealized_pnl)}</strong><span>{money(position.market_value)} market value</span></div><div><strong>{position.thesis_health}/100</strong><span>Thesis health</span></div></div>)}</div> : <div className="position-empty"><div><span className="position-empty-mark"><Icon name="check" /></span><p><b>No positions need intervention</b><small>The monitor activates after an approved paper fill and watches loss, profit, expiration and thesis health.</small></p></div><ol><li><span>01</span>Take profits against the structure’s defined target.</li><li><span>02</span>Exit when the original thesis or risk limit fails.</li><li><span>03</span>Close or roll before the expiration-management window.</li></ol></div>}</section>;
}

function LearningOverview({ learning }: { learning: Dashboard["learning"] }) {
  return <section className="learning-overview"><div className="learning-metrics"><div><span>Reviews recorded</span><strong>{learning.reviewed_decisions}</strong><small>Decision passports</small></div><div><span>Approved previews</span><strong>{learning.approved_previews}</strong><small>Not realised returns</small></div><div><span>Risk vetoes</span><strong>{learning.risk_vetoes}</strong><small>Capital protected</small></div><div><span>Closed outcomes</span><strong>{learning.closed_outcomes} / {learning.minimum_outcomes_for_promotion}</strong><small>Needed before promotion</small></div></div><div className="learning-grid"><article className="surface evidence-chart"><div className="section-heading"><div><h2>Strategy evidence coverage</h2><p>Risk-gate pass rate across recorded reviews—not return performance.</p></div><span>{learning.stage.replaceAll("_", " ")}</span></div><div className="evidence-bars">{learning.candidates.length ? learning.candidates.slice(0, 6).map((candidate) => <div key={candidate.strategy}><span>{strategyName(candidate.strategy)}</span><div><i style={{ width: `${candidate.average_gate_pass_rate * 100}%` }} /></div><strong>{percentage(candidate.average_gate_pass_rate * 100, 0)}</strong><small>{candidate.reviewed} reviews</small></div>) : <div className="empty-state">Run reviewed cycles to build comparable evidence.</div>}</div></article><aside className="surface lesson-card"><div className="section-heading"><div><h2>What the system knows</h2><p>Only statements supported by the ledger.</p></div></div><ul>{learning.lessons.map((lesson) => <li key={lesson}>{lesson}</li>)}</ul></aside></div></section>;
}

function ProposalTable({ plans, onOpen, onReview, busy }: { plans: TradePlan[]; onOpen: (symbol: string) => void; onReview: (symbol: string) => void; busy: boolean }) {
  return <div className="data-table proposal-table">
    <div className="table-row table-heading"><span>Market</span><span>Current reading</span><span>Defined risk</span><span>Conviction</span><span aria-hidden="true" /></div>
    {plans.map((plan) => <div className="table-row" key={plan.symbol}>
      <div className="market-cell"><strong>{plan.symbol}</strong><span>{plan.dte ? `${plan.dte} days` : "No expiry"}</span></div>
      <div className="decision-cell"><button onClick={() => plan.strategy !== "no_trade" && onOpen(plan.symbol)} disabled={plan.strategy === "no_trade"}>{strategyName(plan.strategy)}</button><span>{plan.no_trade_reason || plan.thesis}</span></div>
      <div><strong>{plan.strategy === "no_trade" ? "—" : money(plan.max_loss)}</strong><span>{plan.strategy === "no_trade" ? "No capital committed" : plan.max_profit ? `${(plan.max_profit / Math.max(plan.max_loss, 1)).toFixed(1)}× max return` : "Open upside"}</span></div>
      <div className="conviction"><strong>{Math.round(plan.confidence * 100)}%</strong><span className="confidence-track"><i style={{ width: `${plan.confidence * 100}%` }} /></span></div>
      <button className="plain-icon" disabled={busy} onClick={() => onReview(plan.symbol)} aria-label={`Review ${plan.symbol}`}><Icon name="arrow" size={17} /></button>
    </div>)}</div>;
}

function FanChart({ result }: { result: MonteCarloResult }) {
  const points = result.underlying_bands;
  if (points.length < 2) return null;
  const width = 820, height = 330, left = 52, top = 24, right = 18, bottom = 38;
  const references = [result.spot_price, result.break_even_lower, result.break_even_upper].filter((value): value is number => value !== null);
  const min = Math.min(...points.map((point) => point.p10), ...references) * 0.995;
  const max = Math.max(...points.map((point) => point.p90), ...references) * 1.005;
  const x = (index: number) => left + index * ((width - left - right) / (points.length - 1));
  const y = (value: number) => top + ((max - value) / Math.max(max - min, 0.01)) * (height - top - bottom);
  const line = (key: "p10" | "p25" | "p50" | "p75" | "p90") => points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point[key]).toFixed(1)}`).join(" ");
  const area = (upper: "p90" | "p75", lower: "p10" | "p25") => `${line(upper)} ${[...points].reverse().map((point, reverseIndex) => `L${x(points.length - 1 - reverseIndex).toFixed(1)},${y(point[lower]).toFixed(1)}`).join(" ")} Z`;
  const ticks = [min, (min + max) / 2, max];
  return <div className="chart-frame"><div className="chart-heading"><div><h3>Strategy-conditioned price range</h3><p>{result.scenario_label} · {result.paths.toLocaleString()} paths · focused price scale</p></div><div className="chart-key"><span><i className="wide-key" />80% range</span><span><i className="median-key" />Median</span><span><i className="break-even-key" />Break-even</span></div></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Simulated ${result.symbol} price range over ${result.horizon_days} days under the ${result.scenario_label} scenario`}>
    {ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={left - 9} y={y(tick) + 4} textAnchor="end">{money(tick)}</text></g>)}
    <path d={area("p90", "p10")} className="fan-wide" /><path d={area("p75", "p25")} className="fan-narrow" /><path d={line("p50")} className="fan-median" /><line x1={left} x2={width - right} y1={y(result.spot_price)} y2={y(result.spot_price)} className="spot-line" />{[result.break_even_lower, result.break_even_upper].filter((value): value is number => value !== null).map((value) => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="break-even-line" /><text x={width - right - 3} y={y(value) - 5} textAnchor="end" className="break-even-label">BE {money(value, 2)}</text></g>)}<text x={left} y={height - 10}>Today</text><text x={width - right} y={height - 10} textAnchor="end">Expiry · day {result.horizon_days}</text>
  </svg></div>;
}

function ScenarioAnalysis({ result }: { result: MonteCarloResult }) {
  return <section className="scenario-analysis"><article className="surface outcome-regions"><div className="section-heading"><div><h2>Probability by expiry condition</h2><p>Where the simulated underlying finishes relative to this strategy’s estimated break-even levels.</p></div><span>{result.scenario_label}</span></div><div className="outcome-grid">{result.outcome_scenarios.map((outcome) => <div key={outcome.key} className={outcome.profitable_at_expiry ? "profit-region" : "loss-region"}><div><span>{outcome.label}</span><b>{outcome.profitable_at_expiry ? "Profit region" : "Loss region"}</b></div><strong>{percentage(outcome.probability * 100)}</strong><small>{outcome.condition}</small><p>{outcome.interpretation}</p></div>)}</div></article><article className="surface probability-timeline"><div className="section-heading"><div><h2>When paths reach the profit zone</h2><p>Share of paths profitable if that milestone were expiry—not interim option value.</p></div></div><div className="checkpoint-list">{result.probability_checkpoints.map((point) => <div key={point.day}><span>{point.day === 0 ? "Today" : `Day ${point.day}`}</span><div><i style={{ width: `${point.probability_profitable_if_expiry * 100}%` }} /></div><strong>{percentage(point.probability_profitable_if_expiry * 100, 0)}</strong><small>Median {money(point.median_price, 2)}</small></div>)}</div></article></section>;
}

function DistributionChart({ result }: { result: MonteCarloResult }) {
  const width = 820, height = 250, left = 48, top = 22, right = 16, bottom = 45;
  const maxProbability = Math.max(...result.distribution.map((bin) => bin.probability), 0.01);
  const barWidth = (width - left - right) / result.distribution.length;
  return <div className="chart-frame compact-chart"><div className="chart-heading"><div><h3>Expiry profit and loss</h3><p>Probability across simulated outcomes, after estimated slippage</p></div></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${result.symbol} simulated profit and loss distribution`}><line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} className="chart-axis" />{result.distribution.map((bin, index) => { const midpoint = (bin.lower + bin.upper) / 2; const barHeight = (bin.probability / maxProbability) * (height - top - bottom); return <rect key={`${bin.lower}-${bin.upper}`} x={left + index * barWidth + 2} y={height - bottom - barHeight} width={Math.max(1, barWidth - 4)} height={barHeight} className={midpoint >= 0 ? "profit-bar" : "loss-bar"}><title>{money(bin.lower)} to {money(bin.upper)} · {percentage(bin.probability * 100)}</title></rect>; })}<text x={left} y={height - 16}>{money(result.distribution[0]?.lower || 0)}</text><text x={(width + left - right) / 2} y={height - 16} textAnchor="middle">Outcome P&amp;L</text><text x={width - right} y={height - 16} textAnchor="end">{money(result.distribution.at(-1)?.upper || 0)}</text></svg></div>;
}

function StructureTicket({ result }: { result: MonteCarloResult }) {
  const isEligible = result.proposal_status === "executable";
  const label = isEligible ? "Eligible candidate" : result.proposal_status === "risk_blocked" ? "Risk blocked" : "Research only";
  return <aside className="structure-ticket"><div className="ticket-heading"><div><span>{isEligible ? "Current proposal" : result.proposal_status === "risk_blocked" ? "Rejected proposal" : "Research challenger"}</span><h3>{result.symbol} {strategyName(result.strategy)}</h3></div><span className={`quiet-badge ${result.proposal_status}`}>{label}</span></div>{!isEligible && <p className="eligibility-note"><b>Why it cannot trade now</b>{result.eligibility_reason}</p>}<dl className="ticket-facts"><div><dt>Underlying</dt><dd>{money(result.spot_price, 2)}</dd></div><div><dt>Expiry</dt><dd>{shortDate(result.expiration)}</dd></div><div><dt>Quantity</dt><dd>{result.plan.quantity}</dd></div><div><dt>Net price</dt><dd>{money(result.plan.limit_price || 0, 2)}</dd></div><div><dt>Maximum loss</dt><dd>{money(result.plan.max_loss)}</dd></div><div><dt>Maximum profit</dt><dd>{result.plan.max_profit === null ? "Open" : money(result.plan.max_profit)}</dd></div></dl><div className="leg-list"><span>Structure</span>{result.plan.legs.map((leg) => <div key={leg.symbol}><b className={leg.side}>{leg.side === "buy" ? "Buy" : "Sell"}</b><strong>{leg.ratio_qty} × {leg.strike}{leg.option_type === "call" ? "C" : "P"}</strong><small>Mid {money(leg.mid, 2)} · {percentage(leg.spread_pct)} spread</small></div>)}</div><p className="ticket-note">No order is sent from the Strategy Lab. Simulations are evidence for the autonomous decision loop, never permission to bypass its risk gates.</p></aside>;
}

function Overview({ data, onOpenLab, onReview, running, selected, onSelect }: { data: Dashboard; onOpenLab: (symbol: string) => void; onReview: (symbol: string) => void; running: boolean; selected: Passport | null; onSelect: (passport: Passport) => void }) {
  const gates = selected?.gates || [];
  const passed = gates.filter((gate) => gate.passed).length;
  return <><PageIntro eyebrow="Overview" title="Paper account" copy="A concise view of capital, current proposals and the rules that can stop the desk." side={<p className="as-of">As of {new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}<span>Alpaca paper account</span></p>} /><SummaryStrip data={data} /><OperationsStrip data={data} /><PerformancePanel data={data} /><ScanCoverage data={data} /><MarketBoard data={data} /><div className="main-grid"><section className="surface proposals-surface"><div className="section-heading"><div><h2>Current deep reviews</h2><p>Only the lightweight screen touches all markets; these rows loaded full option-chain evidence.</p></div><span>{data.opportunities.length} of {data.watchlist.length} analyzed</span></div><ProposalTable plans={data.opportunities} onOpen={onOpenLab} onReview={onReview} busy={running} /></section><aside className="surface guardrail-surface"><div className="section-heading"><div><h2>Guardrails</h2><p>Code has final veto authority.</p></div><span className={data.kill_switch ? "state-stopped" : "state-ready"}>{data.kill_switch ? "Paused" : "Active"}</span></div><div className="guardrail-count"><strong>{gates.length ? `${passed} / ${gates.length}` : "Ready"}</strong><span>{gates.length ? "checks passed on selected review" : "Awaiting a reviewed decision"}</span></div><div className="rule-list">{(gates.length ? gates.slice(0, 6) : data.requirements.slice(0, 6).map((item) => ({ name: item.label, passed: item.passed, observed: item.detail }))).map((gate) => <div key={gate.name}><span className={gate.passed ? "rule-pass" : "rule-fail"}>{gate.passed ? <Icon name="check" size={13} /> : <Icon name="alert" size={13} />}</span><p><b>{gate.name}</b><small>{gate.observed}</small></p></div>)}</div></aside></div><PositionMonitor data={data} /><ExecutionTimeline data={data} /><section className="surface recent-surface"><div className="section-heading"><div><h2>Recent decisions</h2><p>The audit trail records proposals, vetoes and paper receipts.</p></div><span>{data.recent_decisions.length} recorded</span></div><div className="decision-rows">{data.recent_decisions.length ? data.recent_decisions.slice(0, 5).map((passport) => <button key={passport.decision_id} onClick={() => onSelect(passport)} className={selected?.decision_id === passport.decision_id ? "selected" : ""}><span className={`decision-mark ${passport.status}`} /><strong>{passport.symbol}</strong><span>{strategyName(passport.plan.strategy)}</span><span className={`decision-status ${passport.status}`}>{passport.status.replaceAll("_", " ")}</span><time>{timeAgo(passport.created_at)}</time><Icon name="arrow" size={15} /></button>) : <div className="empty-state">The first reviewed cycle will appear here.</div>}</div></section></>;
}

function StrategyLab({ data, result, loading, error, paths, setPaths, scenario, setScenario, onRun }: { data: Dashboard; result: MonteCarloResult | null; loading: boolean; error: string; paths: number; setPaths: (paths: number) => void; scenario: SimulationScenario; setScenario: (scenario: SimulationScenario) => void; onRun: (symbol: string, paths?: number, scenario?: SimulationScenario) => void }) {
  const labMarkets = data.watchlist.map((symbol) => ({ symbol, plan: data.opportunities.find((plan) => plan.symbol === symbol) }));
  const activeSymbol = result?.symbol || labMarkets.find((item) => item.plan?.strategy !== "no_trade")?.symbol || labMarkets[0]?.symbol || "";
  const selectScenario = (next: SimulationScenario) => {
    setScenario(next);
    if (activeSymbol) onRun(activeSymbol, paths, next);
  };
  return <><PageIntro eyebrow="Strategy Lab" title="See how the strategy behaves" copy="Explore probability ranges, break-even conditions and expiry outcomes across the expanded trading universe. Symbols outside the current deep shortlist load their option evidence on demand." /><section className="lab-controls" aria-label="Simulation controls"><div className="symbol-tabs">{labMarkets.map((item) => <button key={item.symbol} className={activeSymbol === item.symbol ? "active" : ""} onClick={() => onRun(item.symbol, paths, scenario)}><b>{item.symbol}</b><span>{item.plan ? (item.plan.strategy === "no_trade" ? "Research challenger" : strategyName(item.plan.strategy)) : "Analyze on demand"}</span></button>)}</div><label>Paths<select value={paths} onChange={(event) => setPaths(Number(event.target.value))}><option value={2500}>2,500</option><option value={5000}>5,000</option><option value={10000}>10,000</option><option value={20000}>20,000</option></select></label><button className="secondary-button" onClick={() => activeSymbol && onRun(activeSymbol, paths, scenario)} disabled={loading || !activeSymbol}><Icon name="refresh" size={16} />{loading ? "Running…" : "Run again"}</button></section><section className="scenario-switcher" aria-label="Market condition"><div><span>Market condition</span><small>Change one assumption set at a time</small></div>{scenarioOptions.map((option) => <button key={option.key} className={scenario === option.key ? "active" : ""} onClick={() => selectScenario(option.key)} disabled={loading}><b>{option.label}</b><span>{option.detail}</span></button>)}</section>{error && <div className="notice error-notice"><Icon name="alert" />{error}</div>}{loading && !result ? <div className="lab-loading"><span /><h2>Running the current structure</h2><p>Generating underlying paths and settling each option leg at expiry.</p></div> : result && <><div className={`lab-status-note ${result.proposal_status}`}><span>{result.proposal_status === "research_only" ? "Research-only challenger" : result.proposal_status === "risk_blocked" ? "Risk constitution blocked this structure" : "Eligible trade candidate"}</span><p>{result.proposal_status === "executable" ? "The structure remains subject to fresh evidence and paper-order permission at decision time." : result.eligibility_reason}</p></div><section className="simulation-strip"><div><span>Chance of profit</span><strong>{percentage(result.probability_profit * 100)}</strong><small>{result.paths.toLocaleString()} paths</small></div><div><span>Expected outcome</span><strong className={result.expected_pnl >= 0 ? "gain" : "loss"}>{money(result.expected_pnl)}</strong><small>Mean after costs</small></div><div><span>95% value at risk</span><strong>{money(result.value_at_risk_95)}</strong><small>Loss exceeded in 5% of paths</small></div><div><span>Worst-tail average</span><strong>{money(result.expected_shortfall_95)}</strong><small>Average of worst 5%</small></div><div><span>Near max loss</span><strong>{percentage(result.probability_near_max_loss * 100)}</strong><small>At least 90% of defined loss</small></div></section><div className="lab-grid"><div className="lab-charts"><FanChart result={result} /><DistributionChart result={result} /></div><StructureTicket result={result} /></div><ScenarioAnalysis result={result} /><section className="assumption-panel"><div><p className="overline">Model notes</p><h2>What this run assumes</h2><p>{result.scenario_description} The limits remain visible so the result can be challenged.</p></div><ol>{result.assumptions.map((assumption, index) => <li key={assumption}><span>{index + 1}</span>{assumption}</li>)}</ol><dl><div><dt>Annual volatility</dt><dd>{percentage(result.annual_volatility * 100)}</dd></div><div><dt>Annual drift</dt><dd>{percentage(result.annual_drift * 100, 1, true)}</dd></div><div><dt>Spread cost</dt><dd>{money(result.estimated_slippage)}</dd></div><div><dt>Repeatable seed</dt><dd>{result.seed.toString().slice(-8)}</dd></div></dl></section></>}</>;
}

function IntelligenceView({ intelligence }: { intelligence: Intelligence | null }) {
  if (!intelligence) return <div className="lab-loading"><span /><h2>Gathering market context</h2><p>News, filings and macro series are loading independently.</p></div>;
  return <><PageIntro eyebrow="Intelligence" title="What changed around the watchlist" copy="The evidence is grouped by what a trader needs to verify: the story, the source and when it changed." side={<div className="sentiment-summary"><span>News balance</span><strong>{intelligence.aggregate_sentiment >= 0 ? "Constructive" : "Cautious"}</strong><small>Score {intelligence.aggregate_sentiment >= 0 ? "+" : ""}{intelligence.aggregate_sentiment.toFixed(2)}</small></div>} /><div className="intel-grid"><section className="surface news-surface"><div className="section-heading"><div><h2>Latest company news</h2><p>Highest-relevance items across the current watchlist.</p></div><span>{timeAgo(intelligence.generated_at)}</span></div><div className="article-list">{intelligence.news.slice(0, 10).map((item) => <a href={item.url || undefined} target="_blank" rel="noreferrer" key={`${item.provider}:${item.id}:${item.symbol}`}><span className="article-symbol">{item.symbol}</span><div><h3>{item.headline}</h3><p>{item.summary || `${item.catalyst_type.replaceAll("_", " ")} signal from ${item.source}`}</p><small>{item.source} · {timeAgo(item.published_at)}</small></div><span className={`sentiment-word ${item.sentiment_label}`}>{item.sentiment_label}</span></a>)}</div></section><aside className="intel-side"><section className="surface macro-surface"><div className="section-heading"><div><h2>Macro backdrop</h2><p>Public FRED series</p></div><span>Risk {Math.round(intelligence.macro_risk_score * 100)}/100</span></div><div className="macro-list">{intelligence.macro.map((item) => <div key={item.series_id}><span>{item.label}</span><strong>{item.value.toFixed(2)}{item.unit === "Percent" ? "%" : ""}</strong><small className={item.change > 0 ? "up" : item.change < 0 ? "down" : ""}>{item.change > 0 ? "+" : ""}{item.change.toFixed(2)} vs prior</small></div>)}</div></section><section className="surface source-surface"><div className="section-heading"><div><h2>Source health</h2><p>Provenance before opinion</p></div></div><div className="source-rows">{intelligence.sources.map((source) => <a href={source.url} target="_blank" rel="noreferrer" key={source.name}><span className={source.available ? "source-ok" : "source-off"} /><p><b>{source.name}</b><small>{source.detail}</small></p><Icon name="external" size={14} /></a>)}</div></section></aside></div><section className="surface filing-surface"><div className="section-heading"><div><h2>Recent SEC filings</h2><p>Official records, separated from interpreted news.</p></div><span>{intelligence.filings.length} filings</span></div><div className="filing-table">{intelligence.filings.slice(0, 8).map((filing, index) => <a href={filing.url} target="_blank" rel="noreferrer" key={`${filing.symbol}-${filing.form}-${index}`}><strong>{filing.symbol}</strong><span>{filing.form}</span><p>{filing.description || filing.company}</p><time>{filing.filed_at}</time><Icon name="external" size={14} /></a>)}</div></section></>;
}

function Journal({ data, selected, onSelect }: { data: Dashboard; selected: Passport | null; onSelect: (passport: Passport) => void }) {
  return <><PageIntro eyebrow="Decision journal" title="A memory the desk can learn from" copy="Every review preserves the original evidence and the reason capital moved—or stayed in cash. Outcome grading begins only after a position closes." /><LearningOverview learning={data.learning} /><ExecutionTimeline data={data} /><div className="journal-grid"><section className="surface journal-list"><div className="section-heading"><div><h2>Recorded decisions</h2><p>Newest first</p></div><span>{data.recent_decisions.length} entries</span></div>{data.recent_decisions.map((passport) => <button key={passport.decision_id} className={selected?.decision_id === passport.decision_id ? "selected" : ""} onClick={() => onSelect(passport)}><span className={`decision-mark ${passport.status}`} /><div><b>{passport.symbol} · {strategyName(passport.plan.strategy)}</b><small>{shortDate(passport.created_at)} · {passport.status.replaceAll("_", " ")}</small></div><Icon name="arrow" size={15} /></button>)}</section><section className="surface journal-detail">{selected ? <><div className="detail-head"><div><p className="overline">Decision passport</p><h2>{selected.symbol} · {strategyName(selected.plan.strategy)}</h2></div><span className={`decision-status ${selected.status}`}>{selected.status.replaceAll("_", " ")}</span></div><p className="detail-thesis">{selected.plan.thesis}</p><div className="detail-facts"><div><span>Confidence</span><strong>{percentage(selected.plan.confidence * 100)}</strong></div><div><span>Maximum loss</span><strong>{money(selected.plan.max_loss)}</strong></div><div><span>Checks passed</span><strong>{selected.gates.filter((gate) => gate.passed).length} / {selected.gates.length}</strong></div><div><span>Committee support</span><strong>{selected.opinions.filter((opinion) => opinion.verdict === "support").length} / {selected.opinions.length}</strong></div></div><h3>Reasoning record</h3><div className="opinion-list">{selected.opinions.map((opinion) => <div key={opinion.agent}><span className={`opinion-state ${opinion.verdict}`}>{opinion.verdict}</span><p><b>{opinion.agent}<em>{opinion.model}</em></b><small>{opinion.summary}</small></p></div>)}</div><div className="learning-state"><Icon name="journal" /><div><b>Outcome lesson is pending</b><p>The system will compare the thesis with realised P&amp;L after closure. It will not invent a lesson from an open or previewed trade.</p></div></div><footer className="hash-footer"><span>Audit hash</span><code>{selected.record_hash || "Pending ledger write"}</code></footer></> : <div className="empty-state large">Select a decision to inspect its evidence.</div>}</section></div></>;
}

export default function HomePage() {
  const [view, setView] = useState<View>("overview");
  const [data, setData] = useState<Dashboard | null>(null);
  const [intelligence, setIntelligence] = useState<Intelligence | null>(null);
  const [marketPulse, setMarketPulse] = useState<MarketPulse | null>(null);
  const [marketPulseError, setMarketPulseError] = useState("");
  const [selected, setSelected] = useState<Passport | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [labResult, setLabResult] = useState<MonteCarloResult | null>(null);
  const [labLoading, setLabLoading] = useState(false);
  const [labError, setLabError] = useState("");
  const [paths, setPaths] = useState(5_000);
  const [scenario, setScenario] = useState<SimulationScenario>("base");
  const load = useCallback(async () => { const response = await fetch(`${API}/api/dashboard`, { cache: "no-store" }); if (!response.ok) throw new Error("The paper desk is not available right now."); const payload: Dashboard = await response.json(); setData(payload); setSelected((current) => current || payload.recent_decisions[0] || null); }, []);
  const loadMarketPulse = useCallback(async () => { const response = await fetch(`${API}/api/market-pulse`, { cache: "no-store" }); if (!response.ok) throw new Error("The broad market feed is temporarily unavailable."); const payload: MarketPulse = await response.json(); setMarketPulse(payload); setMarketPulseError(""); }, []);
  const operatorFetch = async (path: string, init: RequestInit) => {
    const headers = new Headers(init.headers);
    if (data?.mode === "paper") {
      let key = window.sessionStorage.getItem("volition-operator-key") || "";
      if (!key) key = window.prompt("Enter the Volition operator access key")?.trim() || "";
      if (!key) throw new Error("Operator access is required for state-changing controls.");
      window.sessionStorage.setItem("volition-operator-key", key);
      headers.set("X-Volition-Operator-Key", key);
    }
    const response = await fetch(`${API}${path}`, { ...init, headers });
    if (response.status === 401) {
      window.sessionStorage.removeItem("volition-operator-key");
      throw new Error("The operator access key was not accepted. Try the action again to re-enter it.");
    }
    if (response.status === 503) throw new Error("Operator controls are locked on this deployment.");
    return response;
  };
  useEffect(() => { load().catch((reason) => setError(reason.message)); void loadMarketPulse().catch((reason) => setMarketPulseError(reason.message)); const dashboardRefresh = window.setInterval(() => void load().catch(() => undefined), 60_000); const pulseRefresh = window.setInterval(() => void loadMarketPulse().catch(() => undefined), 60_000); fetch(`${API}/api/intelligence`, { cache: "no-store" }).then((response) => response.ok ? response.json() as Promise<Intelligence> : Promise.reject()).then(setIntelligence).catch(() => setIntelligence(null)); return () => { window.clearInterval(dashboardRefresh); window.clearInterval(pulseRefresh); }; }, [load, loadMarketPulse]);
  const runLab = useCallback(async (symbol: string, requestedPaths?: number, requestedScenario?: SimulationScenario) => { if (!symbol) return; setLabLoading(true); setLabError(""); try { const response = await fetch(`${API}/api/strategy-lab`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, paths: requestedPaths || paths, scenario: requestedScenario || scenario }) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || "The simulation could not run."); setLabResult(body as MonteCarloResult); } catch (reason) { setLabError(reason instanceof Error ? reason.message : "The simulation could not run."); } finally { setLabLoading(false); } }, [paths, scenario]);
  const openLab = (symbol: string) => { setView("lab"); void runLab(symbol, paths, scenario); };
  const runCycle = async (symbol?: string) => { setRunning(true); setError(""); try { const response = await operatorFetch("/api/cycle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: symbol || null }) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || "The review could not complete."); setSelected(body as Passport); await load(); setView("journal"); } catch (reason) { setError(reason instanceof Error ? reason.message : "The review could not complete."); } finally { setRunning(false); } };
  const toggleKill = async () => { if (!data) return; setError(""); try { const response = await operatorFetch("/api/kill-switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ armed: !data.kill_switch }) }); if (response.ok) setData({ ...data, kill_switch: !data.kill_switch }); } catch (reason) { setError(reason instanceof Error ? reason.message : "The operator control could not complete."); } };
  const navigation: Array<{ key: View; label: string; detail: string; icon: string }> = useMemo(() => [{ key: "overview", label: "Overview", detail: "Account and proposals", icon: "overview" }, { key: "markets", label: "Market pulse", detail: "Indices, commodities, sectors", icon: "markets" }, { key: "lab", label: "Strategy Lab", detail: "Simulate outcomes", icon: "lab" }, { key: "intelligence", label: "Intelligence", detail: "News, macro, filings", icon: "intelligence" }, { key: "journal", label: "Decision journal", detail: "Evidence and lessons", icon: "journal" }], []);
  if (!data) return <main className="loading-screen"><div className="wordmark"><span>V</span>Volition</div><p>{error || "Opening the paper desk…"}</p></main>;
  const schedulerLabel = data.scheduler_state === "running" ? "Scheduler active" : data.scheduler_state === "paused" ? "Scheduler paused" : "Scheduler waiting";
  const operatorLocked = data.mode === "paper" && !data.operator_controls_configured;
  return <main className="app-shell"><aside className="sidebar"><div className="wordmark"><span>V</span><div>Volition<small>Options risk desk</small></div></div><nav>{navigation.map((item) => <button key={item.key} onClick={() => { setView(item.key); if (item.key === "lab" && !labResult) { const first = data.opportunities.find((plan) => plan.strategy !== "no_trade") || data.opportunities[0]; if (first) void runLab(first.symbol, paths, scenario); } }} className={view === item.key ? "active" : ""} aria-current={view === item.key ? "page" : undefined}><Icon name={item.icon} /><span><b>{item.label}</b><small>{item.detail}</small></span></button>)}</nav><div className="desk-card"><span className={data.mode === "paper" ? "live-dot" : "demo-dot"} /><div><b>{data.mode === "paper" ? "Paper desk connected" : "Demonstration data"}</b><small>{data.order_submission_enabled ? "Order authority enabled" : "Order authority locked"} · {schedulerLabel}</small></div></div><p className="sidebar-note">Defined-risk options only.<br />{data.order_submission_enabled ? "Autonomous paper orders enabled." : "Reviews run; no new orders can be sent."}</p></aside><section className="workspace"><header className="workspace-bar"><div className="mobile-wordmark"><span>V</span>Volition</div><SessionCountdown data={data} schedulerLabel={schedulerLabel} /><div><button className={`pause-button ${data.kill_switch ? "armed" : ""}`} onClick={toggleKill} disabled={operatorLocked} title={operatorLocked ? "Operator controls are locked on the public deployment" : undefined}><Icon name={data.kill_switch ? "play" : "pause"} size={15} />{operatorLocked ? "Operator locked" : data.kill_switch ? "Resume" : data.order_submission_enabled ? "Pause orders" : "Pause reviews"}</button><button className="primary-button" onClick={() => void runCycle()} disabled={running || data.kill_switch || operatorLocked} title={operatorLocked ? "Operator controls are locked on the public deployment" : undefined}><Icon name="refresh" size={16} />{running ? "Reviewing…" : operatorLocked ? "Read-only demo" : "Run market review"}</button></div></header><div className="mobile-nav">{navigation.map((item) => <button key={item.key} className={view === item.key ? "active" : ""} onClick={() => { setView(item.key); if (item.key === "lab" && !labResult) { const first = data.opportunities.find((plan) => plan.strategy !== "no_trade") || data.opportunities[0]; if (first) void runLab(first.symbol, paths, scenario); } }}>{item.label}</button>)}</div><MarketTape pulse={marketPulse} onOpen={() => setView("markets")} /><div className="content">{error && <div className="notice error-notice"><Icon name="alert" />{error}</div>}{view === "overview" && <Overview data={data} onOpenLab={openLab} onReview={(symbol) => void runCycle(symbol)} running={running || operatorLocked} selected={selected} onSelect={(passport) => { setSelected(passport); setView("journal"); }} />}{view === "markets" && <MarketPulseView pulse={marketPulse} error={marketPulseError} />}{view === "lab" && <StrategyLab data={data} result={labResult} loading={labLoading} error={labError} paths={paths} setPaths={setPaths} scenario={scenario} setScenario={setScenario} onRun={(symbol, count, nextScenario) => void runLab(symbol, count, nextScenario)} />}{view === "intelligence" && <IntelligenceView intelligence={intelligence} />}{view === "journal" && <Journal data={data} selected={selected} onSelect={setSelected} />}</div></section></main>;
}
