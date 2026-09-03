import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "file:///C:/Users/yashk/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const ROOT = "E:/Hackathons/devpost/quantsentinel+";
const ASSETS = path.join(ROOT, "docs", "assets");
const OUT = path.join(ROOT, "docs", "deck-render");
const FINAL = path.join(ROOT, "docs", "Volition_Hackathon_Deck.pptx");

const C = {
  paper: "#F5F1E6",
  white: "#FFFFFF",
  ink: "#17231D",
  muted: "#667169",
  rule: "#CCD1CA",
  forest: "#173D2A",
  sage: "#DDE5DC",
  pale: "#EEF0EB",
  amber: "#B7791F",
  blue: "#416C8A",
  red: "#A84A3F",
};

const FONT = "Arial";
const DISPLAY = "Georgia";

async function bytes(file) {
  const b = await fs.readFile(file);
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
}

async function writeBlob(file, blob) {
  await fs.writeFile(file, new Uint8Array(await blob.arrayBuffer()));
}

function box(slide, x, y, w, h, fill = C.white, line = C.rule, radius = 0) {
  return slide.shapes.add({
    geometry: radius ? "roundRect" : "rect",
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: line, width: line === "none" ? 0 : 1 },
    ...(radius ? { borderRadius: radius } : {}),
  });
}

function text(slide, value, x, y, w, h, size = 24, color = C.ink, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = value;
  shape.text.style = {
    fontSize: size,
    color,
    typeface: opts.typeface ?? FONT,
    bold: opts.bold ?? false,
    italic: opts.italic ?? false,
    alignment: opts.alignment ?? "left",
    verticalAlignment: opts.verticalAlignment ?? "top",
    autoFit: opts.autoFit ?? "shrinkText",
  };
  return shape;
}

function rule(slide, x, y, w, color = C.rule, width = 1) {
  return slide.shapes.add({
    geometry: "straightConnector1",
    position: { left: x, top: y, width: w, height: 0 },
    fill: "none",
    line: { style: "solid", fill: color, width },
  });
}

function dot(slide, x, y, diameter, fill = C.forest) {
  return slide.shapes.add({
    geometry: "ellipse",
    position: { left: x, top: y, width: diameter, height: diameter },
    fill,
    line: { style: "solid", fill, width: 0 },
  });
}

function header(slide, section, titleValue, page) {
  text(slide, `VOLITION / ${section.toUpperCase()}`, 54, 36, 520, 22, 12, C.forest, { bold: true });
  text(slide, titleValue, 54, 70, 1110, 70, 42, C.ink, { typeface: DISPLAY, bold: false });
  rule(slide, 54, 145, 1172, C.rule, 1);
  text(slide, String(page).padStart(2, "0"), 1178, 670, 48, 18, 11, C.muted, { alignment: "right" });
}

function addBullet(slide, label, x, y, w, color = C.ink, size = 20) {
  dot(slide, x, y + 8, 8, C.amber);
  text(slide, label, x + 20, y, w - 20, 48, size, color, { verticalAlignment: "middle" });
}

function addNotes(slide, body, sources = []) {
  const lines = [body];
  if (sources.length) {
    lines.push("", "Sources:", ...sources.map((source) => `- ${source}`));
  }
  slide.speakerNotes.textFrame.setText(lines.join("\n"));
}

async function addImage(slide, file, alt, position, fit = "cover", radius = 0, crop) {
  return slide.images.add({
    blob: await bytes(file),
    contentType: "image/png",
    alt,
    fit,
    position,
    geometry: radius ? "roundRect" : "rect",
    ...(radius ? { borderRadius: radius } : {}),
    ...(crop ? { crop } : {}),
  });
}

const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });

// 1 — Cover. Codex Grid slide-08 silhouette: half narrative, half visual field.
{
  const slide = deck.slides.add();
  slide.background.fill = C.paper;
  await addImage(
    slide,
    path.join(ASSETS, "volition-cover-art.png"),
    "Editorial illustration of branching market paths passing through a risk-control aperture",
    { left: 0, top: 0, width: 1280, height: 720 },
  );
  box(slide, 0, 0, 630, 720, "#F5F1E6E8", "none");
  text(slide, "ALPACA AI TRADING AGENTS HACKATHON", 60, 58, 520, 24, 13, C.forest, { bold: true });
  text(slide, "Volition", 60, 158, 520, 92, 70, C.ink, { typeface: DISPLAY });
  text(slide, "The autonomous options desk\nthat knows when not to trade.", 60, 260, 510, 116, 30, C.ink, { typeface: DISPLAY });
  rule(slide, 60, 418, 160, C.amber, 3);
  text(slide, "Private AI committee · deterministic risk veto · Alpaca paper lifecycle · tamper-evident memory", 60, 450, 500, 92, 18, C.muted);
  text(slide, "Hosted judge build  •  September 2026", 60, 640, 430, 24, 13, C.forest, { bold: true });
  addNotes(slide, "Open on the central tension: autonomy is valuable only when the system can prove why it acted—or refused.", [
    "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon",
  ]);
}

// 2 — Problem. Codex Grid slide-10 paired narrative.
{
  const slide = deck.slides.add();
  slide.background.fill = C.white;
  header(slide, "Problem", "A recommendation is not an autonomous trading system", 2);
  text(slide, "Most agent demos end at “buy” or “sell.” The hard part starts one second later.", 54, 180, 620, 80, 28, C.ink, { typeface: DISPLAY });
  text(slide, "Without governed execution, the model can overreach, broker state can drift, and a backtest can be mistaken for real performance.", 54, 286, 580, 126, 20, C.muted);
  const rows = [
    ["Risk", "Who can veto the model?"],
    ["Broker", "Was it accepted, filled, or cancelled?"],
    ["Memory", "Did the system learn from verified outcomes?"],
  ];
  rows.forEach((row, i) => {
    const y = 192 + i * 122;
    box(slide, 720, y, 506, 92, i === 0 ? C.sage : C.pale, "none", 8);
    text(slide, row[0], 744, y + 18, 112, 24, 14, C.forest, { bold: true });
    text(slide, row[1], 744, y + 46, 440, 30, 20, C.ink, { bold: true });
  });
  text(slide, "Volition was designed around those three missing layers.", 54, 565, 600, 42, 22, C.forest, { bold: true });
  addNotes(slide, "Frame the project as a complete operating loop rather than another signal generator. The product's differentiation is governance, reconciliation, and evidence.");
}

// 3 — Architecture. Codex Grid process silhouette.
{
  const slide = deck.slides.add();
  slide.background.fill = C.paper;
  header(slide, "System", "Action begins only after two independent veto layers", 3);
  const nodes = [
    ["01", "Alpaca evidence", "Account, clock, bars, chains, news"],
    ["02", "Strategy router", "Defined-risk spread or cash"],
    ["03", "Private Qwen", "Three adversarial opinions"],
    ["04", "Risk gates", "16 deterministic checks"],
    ["05", "Paper lifecycle", "Submit, reconcile, manage exit"],
    ["06", "Audit memory", "Passport, receipts, verified outcomes"],
  ];
  nodes.forEach((node, i) => {
    const x = 52 + i * 202;
    if (i < nodes.length - 1) {
      rule(slide, x + 154, 355, 52, C.amber, 2);
      slide.shapes.add({ geometry: "chevron", position: { left: x + 184, top: 347, width: 20, height: 17 }, fill: C.amber, line: { style: "solid", fill: C.amber, width: 0 } });
    }
    box(slide, x, 235, 166, 242, i === 3 ? C.forest : C.white, i === 3 ? C.forest : C.rule, 8);
    text(slide, node[0], x + 18, 254, 52, 24, 13, i === 3 ? C.paper : C.forest, { bold: true });
    text(slide, node[1], x + 18, 303, 130, 60, 22, i === 3 ? C.white : C.ink, { typeface: DISPLAY });
    text(slide, node[2], x + 18, 389, 132, 66, 14, i === 3 ? C.sage : C.muted);
  });
  text(slide, "AI interprets evidence. Code alone owns structure, size, permission, and final authority.", 54, 540, 1120, 54, 25, C.ink, { typeface: DISPLAY });
  addNotes(slide, "Walk left to right. Emphasize that the AI committee cannot invent option symbols, resize an order, or bypass a gate.", [
    "https://docs.alpaca.markets/us/docs/alpacas-cli",
    "https://alpaca.markets/learn/building-nightwatcher-v2-a-multi-agent-trading-system-with-alpaca",
  ]);
}

// 4 — Boundary between model and deterministic code. Checklist silhouette.
{
  const slide = deck.slides.add();
  slide.background.fill = C.white;
  header(slide, "Governance", "The model can argue. It cannot grant itself permission.", 4);
  text(slide, "AI committee", 54, 182, 510, 46, 28, C.forest, { typeface: DISPLAY });
  text(slide, "Three roles receive bounded market and structure evidence through a private Qwen endpoint.", 54, 236, 510, 72, 19, C.muted);
  ["Regime Sentinel challenges direction", "Volatility Architect checks structure", "Adversarial Skeptic argues for cash"].forEach((b, i) => addBullet(slide, b, 54, 336 + i * 62, 520));
  box(slide, 668, 175, 558, 408, C.forest, C.forest, 10);
  text(slide, "Deterministic constitution", 704, 207, 470, 44, 28, C.white, { typeface: DISPLAY });
  text(slide, "Final authority stays in tested code.", 704, 257, 460, 32, 18, C.sage);
  const checks = ["Defined-risk geometry", "Liquidity + fresh quotes", "Account + options level", "Drawdown + concentration", "Monte Carlo veto", "Duplicate + market-window checks"];
  checks.forEach((b, i) => {
    dot(slide, 706, 324 + i * 39, 10, C.amber);
    text(slide, b, 730, 316 + i * 39, 430, 30, 18, C.white);
  });
  text(slide, "Fallback is explicit, bounded, and visible—not silent.", 54, 612, 720, 36, 20, C.red, { bold: true });
  addNotes(slide, "The committee is useful because each role makes a different argument. The constitution is useful because it is not an argument: every gate is deterministic and testable.");
}

// 5 — Live overview evidence. Codex Grid half-image field.
{
  const slide = deck.slides.add();
  slide.background.fill = C.paper;
  header(slide, "Live proof", "The deployed desk is connected, autonomous—and intentionally locked", 5);
  box(slide, 50, 171, 860, 470, C.white, C.rule, 8);
  await addImage(slide, path.join(ASSETS, "volition-live-overview.png"), "Live Volition overview showing Alpaca account, private model, scheduler, and performance", { left: 58, top: 179, width: 844, height: 454 }, "cover", 5);
  const stats = [
    ["$100k", "fresh paper account"],
    ["20", "tradable symbols"],
    ["Qwen", "private committee"],
    ["Locked", "order authority"],
  ];
  stats.forEach((s, i) => {
    const y = 177 + i * 110;
    text(slide, s[0], 956, y, 250, 42, 30, i === 3 ? C.amber : C.forest, { typeface: DISPLAY });
    text(slide, s[1], 956, y + 45, 250, 27, 15, C.muted);
    if (i < stats.length - 1) rule(slide, 956, y + 88, 250, C.rule, 1);
  });
  addNotes(slide, "This is a live screenshot from the public judge build. The scheduler continues server-side; public buttons are disabled because operator actions fail closed without a separate key.", [
    "https://instance-20260318-1838.tail042e87.ts.net/",
  ]);
}

// 6 — Monte Carlo lab evidence.
{
  const slide = deck.slides.add();
  slide.background.fill = C.white;
  header(slide, "Stress testing", "Monte Carlo adds a veto, never a permission slip", 6);
  box(slide, 50, 173, 866, 464, C.paper, C.rule, 8);
  await addImage(slide, path.join(ASSETS, "volition-live-strategy-lab.png"), "Live Strategy Lab showing probability metrics and a risk-blocked structure", { left: 58, top: 181, width: 850, height: 448 }, "cover", 5);
  const callouts = [
    ["5,000 paths", "reproducible scenario run"],
    ["39.0%", "chance of profit"],
    ["Blocked", "constitution outcome"],
  ];
  callouts.forEach((s, i) => {
    const y = 188 + i * 126;
    text(slide, s[0], 962, y, 248, 42, 29, i === 2 ? C.red : C.forest, { typeface: DISPLAY });
    text(slide, s[1], 962, y + 47, 244, 46, 15, C.muted);
  });
  text(slide, "Price fan · expiry P&L · VaR · expected shortfall · near-max-loss probability", 954, 565, 260, 72, 14, C.ink, { bold: true });
  addNotes(slide, "The lab works across the expanded universe and exposes assumptions, costs, and model limitations. This visible example is rejected because chain liquidity and stress criteria fail.", [
    "https://instance-20260318-1838.tail042e87.ts.net/",
  ]);
}

// 7 — Performance + learning. Metric-grid silhouette plus native chart.
{
  const slide = deck.slides.add();
  slide.background.fill = C.paper;
  header(slide, "Evidence", "Performance and learning begin with broker receipts—not imagination", 7);
  const chartFrame = box(slide, 54, 180, 696, 400, C.white, C.rule, 8);
  slide.charts.add("line", {
    position: { left: chartFrame.position.left + 28, top: chartFrame.position.top + 40, width: chartFrame.position.width - 56, height: 290 },
    categories: ["Aug 28", "Aug 29", "Sep 1", "Sep 2"],
    series: [
      { name: "Volition", values: [100000, 100000, 100000, 100000], fill: C.forest },
      { name: "SPY benchmark", values: [100000, 100000, 99005.56, 99462.48], fill: C.blue },
    ],
    hasLegend: true,
    yAxis: { majorGridlines: { style: "solid", fill: C.rule, width: 1 } },
  });
  text(slide, "4 broker observations · 0.00% account return · +0.54% versus SPY", 80, 530, 630, 26, 15, C.muted, { bold: true });
  const cards = [
    ["53", "reviews recorded", C.forest],
    ["43", "risk vetoes", C.red],
    ["0 / 5", "verified closes needed", C.amber],
  ];
  cards.forEach((s, i) => {
    const y = 180 + i * 132;
    box(slide, 794, y, 432, 108, C.white, "none", 8);
    text(slide, s[0], 820, y + 21, 126, 45, 32, s[2], { typeface: DISPLAY });
    text(slide, s[1], 954, y + 31, 238, 30, 18, C.ink, { bold: true });
  });
  text(slide, "No preview is labelled as realised P&L. Promotion stays locked until five broker-verified closed outcomes exist.", 796, 589, 420, 54, 16, C.muted);
  addNotes(slide, "The chart uses Alpaca portfolio history aligned by date with SPY IEX bars. The account has not submitted orders, so the flat equity and zero P&L are the honest result—not a missing demo prop.", [
    "https://instance-20260318-1838.tail042e87.ts.net/api/dashboard",
  ]);
}

// 8 — Hackathon fit. Metrics/checklist silhouette.
{
  const slide = deck.slides.add();
  slide.background.fill = C.white;
  header(slide, "Judge fit", "Every required primitive is visible in one coherent product", 8);
  const items = [
    ["Autonomous options", "15-minute scheduler, multi-strategy router, managed exits"],
    ["Alpaca-native", "CLI evidence plane plus atomic mleg Trading API path"],
    ["Fresh competition account", "$100,000 paper balance and options Level 3"],
    ["AI reasoning", "Private Qwen committee with per-opinion provenance"],
    ["Risk + transparency", "Deterministic vetoes, hashes, lifecycle receipts"],
    ["Public delivery", "HTTPS judge build, responsive UI, read-only controls"],
  ];
  items.forEach((item, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = 54 + col * 596;
    const y = 181 + row * 142;
    box(slide, x, y, 550, 112, row === 1 ? C.paper : C.pale, "none", 6);
    dot(slide, x + 22, y + 25, 15, C.forest);
    text(slide, "✓", x + 23, y + 21, 14, 18, 11, C.white, { bold: true, alignment: "center" });
    text(slide, item[0], x + 52, y + 18, 470, 28, 19, C.ink, { bold: true });
    text(slide, item[1], x + 52, y + 52, 468, 45, 15, C.muted);
  });
  text(slide, "The only deliberately unfinished proof is a broker-verified entry → fill → managed exit. Order submission remains off until the team explicitly authorises that paper-account run.", 54, 620, 1120, 42, 16, C.red, { bold: true });
  addNotes(slide, "This slide maps the product directly to the hackathon requirements. Be explicit that paper order submission is implemented but intentionally locked pending team authorization.", [
    "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon",
    "https://docs.alpaca.markets/us/docs/alpacas-cli",
  ]);
}

// 9 — Close. Sparse closing composition, not a generic thank-you.
{
  const slide = deck.slides.add();
  slide.background.fill = C.forest;
  text(slide, "VOLITION", 58, 52, 320, 24, 13, C.sage, { bold: true });
  text(slide, "The agent judges can trust\nwhen it says no.", 58, 150, 850, 166, 54, C.white, { typeface: DISPLAY });
  rule(slide, 58, 356, 160, C.amber, 3);
  text(slide, "Autonomous options decisions. Private adversarial reasoning. Deterministic risk. Broker-backed evidence. Honest learning.", 58, 398, 820, 98, 24, C.sage);
  box(slide, 930, 142, 278, 292, "#244B38", "#55705F", 8);
  text(slide, "LIVE", 956, 170, 100, 20, 12, C.amber, { bold: true });
  text(slide, "Paper account", 956, 220, 220, 28, 19, C.white, { bold: true });
  text(slide, "Private model", 956, 272, 220, 28, 19, C.white, { bold: true });
  text(slide, "Scheduler active", 956, 324, 220, 28, 19, C.white, { bold: true });
  text(slide, "Orders locked", 956, 376, 220, 28, 19, C.amber, { bold: true });
  text(slide, "instance-20260318-1838.tail042e87.ts.net", 58, 624, 780, 24, 15, C.white, { bold: true });
  text(slide, "Alpaca AI Trading Agents Hackathon · 2026", 58, 654, 600, 20, 12, C.sage);
  addNotes(slide, "Close by resolving the opening tension: Volition is autonomous because it can act, and trustworthy because it can refuse—and prove both.", [
    "https://instance-20260318-1838.tail042e87.ts.net/",
  ]);
}

await fs.mkdir(OUT, { recursive: true });
for (const [index, slide] of deck.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  await writeBlob(path.join(OUT, `${stem}.png`), await deck.export({ slide, format: "png", scale: 1 }));
  const layout = await slide.export({ format: "layout" });
  await fs.writeFile(path.join(OUT, `${stem}.layout.json`), await layout.text());
}
await writeBlob(path.join(OUT, "deck-montage.webp"), await deck.export({ format: "webp", montage: true, scale: 1 }));
const pptx = await PresentationFile.exportPptx(deck);
await pptx.save(FINAL);
await fs.copyFile(path.join(OUT, "slide-01.png"), path.join(ROOT, "docs", "Volition_Hackathon_Cover.png"));
console.log(JSON.stringify({ pptx: FINAL, slides: deck.slides.items.length, renderDir: OUT }));
