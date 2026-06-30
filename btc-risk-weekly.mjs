// btc-risk-weekly.mjs
// =============================================================================
// SHARED RISK LIBRARY for bitcoinrisk.net — v2 11-signal model.
//
// Single source of truth: it reads the SAME data.json the dashboard loads, which is
// regenerated daily by btc_risk_model_v2.py (GitHub Action). There is NO embedded
// model or price seed here anymore, so nothing to keep in sync by hand.
//
// Exports:
//   getLatestRisk()                    -> { date, risk, price, conf, live }
//   actionFor(risk)                    -> gauge regime (buy/hold/sell + mult/parts)
//   strategyBuy(strategy, risk, base)  -> { mult, amount, save } per the dashboard's BS table
//   labelsFor(risk, lang)             -> { regime, color } bilingual
//
// getLatestRisk holds the 9 on-chain sub-scores at the snapshot in data.json and
// recomputes Mayer + ATH-drawdown against the live BTC price — identical to the
// dashboard's "live" math — so an email and the site agree to within rounding.
//
// Env:
//   DATA_URL           where data.json lives (default https://bitcoinrisk.net/data.json)
//   COINGECKO_API_KEY  optional, for the live daily price fill
// Node 20+ (global fetch). Zero dependencies.
// NOT FINANCIAL ADVICE.
// =============================================================================

const DATA_URL = process.env.DATA_URL || "https://bitcoinrisk.net/data.json";

/* ===== math (identical to the dashboard) ===== */
const clip = (x, a, b) => Math.max(a, Math.min(b, x));
const sig  = (x, c, b) => 1 / (1 + Math.exp(-(x - c) / b));
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
const stdev = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) ** 2))); };
const todayISO = () => new Date().toISOString().slice(0, 10);

function riskAt(model, prices, i, prevRisk, runMax) {
  const P = prices[i];
  const m0 = Math.max(0, i - (model.smaMayer - 1));
  const sMayer = clip(sig(P / mean(prices.slice(m0, i + 1)), model.params.mayer[0], model.params.mayer[1]), 0, 1);
  const sAth = clip(Math.pow(Math.min(P / runMax, 1), model.athExp), 0, 1);
  const subs = { mayer: sMayer, ath: sAth };
  for (const k in model.heldSubs) subs[k] = model.heldSubs[k];
  let num = 0, den = 0;
  for (const k in model.weights) { const v = subs[k]; if (v != null && !isNaN(v)) { num += model.weights[k] * v; den += model.weights[k]; } }
  const raw = den > 0 ? num / den : prevRisk;
  const alpha = 2 / (model.emaSpan + 1);
  const risk = clip(alpha * raw + (1 - alpha) * prevRisk, 0, 1);
  const arr = Object.values(subs).filter((v) => v != null && !isNaN(v));
  const disp = stdev(arr);
  const conf = Math.round(clip(10 * (0.78 * clip(1 - 2 * disp, 0, 1) + 0.22 * clip(2 * Math.abs(risk - 0.5), 0, 1)), 0, 10));
  return { risk, conf };
}

/* ===== gauge regime (verbatim from the dashboard's actionFor) ===== */
export function actionFor(r) {
  if (r < 0.10) return { kind: "buy", mult: 5 };
  if (r < 0.20) return { kind: "buy", mult: 4 };
  if (r < 0.30) return { kind: "buy", mult: 3 };
  if (r < 0.60) return { kind: "hold" };
  if (r < 0.70) return { kind: "sell", parts: 2 };
  if (r < 0.80) return { kind: "sell", parts: 3 };
  if (r < 0.90) return { kind: "sell", parts: 4 };
  return { kind: "sell", parts: 5 };
}

/* ===== per-strategy DCA buy multiplier (verbatim from the dashboard's BS table) ===== */
export function strategyBuy(strategy, risk, base) {
  const b = (Number.isFinite(base) && base > 0) ? base : 100;
  let mult;
  if (strategy === "cons") {
    mult = risk < 0.10 ? 2.5 : risk < 0.20 ? 2.0 : risk < 0.30 ? 1.5 : 1.0;
  } else if (strategy === "aggr") {
    mult = risk < 0.15 ? 10 : risk < 0.25 ? 7 : risk < 0.35 ? 3 : 0;
  } else {
    mult = risk < 0.10 ? 5 : risk < 0.20 ? 4 : risk < 0.30 ? 3 : 0;
  }
  return { mult, amount: Math.round(mult * b), save: mult === 0 };
}

/* ===== bilingual regime label + colour ===== */
export function labelsFor(r, lang) {
  const es = lang === "es";
  const a = actionFor(r);
  if (a.kind === "buy")  return { regime: es ? "Acumulaci\u00F3n" : "Accumulation", color: "#3fb950" };
  if (a.kind === "hold") return { regime: "Neutral", color: "#e7b53b" };
  return { regime: es ? "Distribuci\u00F3n" : "Distribution", color: "#f0883e" };
}

/* ===== live price (same multi-source chain as the dashboard) ===== */
async function fetchSpot() {
  const tries = [
    async () => { const r = await fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot"); if (!r.ok) throw 0; const j = await r.json(); return parseFloat(j.data.amount); },
    async () => { const r = await fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD"); if (!r.ok) throw 0; const j = await r.json(); const k = Object.keys(j.result)[0]; return parseFloat(j.result[k].c[0]); },
    async () => { const r = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"); if (!r.ok) throw 0; const j = await r.json(); return j.bitcoin.usd; },
  ];
  for (const f of tries) { try { const v = await f(); if (v > 0) return v; } catch (e) {} }
  return null;
}
async function fetchDailyFill(lastDate) {
  try {
    const hdr = process.env.COINGECKO_API_KEY ? { "x-cg-demo-api-key": process.env.COINGECKO_API_KEY } : {};
    const r = await fetch("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365", { headers: hdr, cache: "no-store" });
    if (r.ok) {
      const j = await r.json(); const byDay = {};
      for (const a of j.prices) byDay[new Date(a[0]).toISOString().slice(0, 10)] = a[1];
      return Object.keys(byDay).sort().map((d) => ({ date: d, price: byDay[d] })).filter((p) => p.date > lastDate);
    }
  } catch (e) {}
  return [];
}

/* ===== PUBLIC: getLatestRisk() — reads data.json, returns the dashboard's live number ===== */
export async function getLatestRisk() {
  const res = await fetch(DATA_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`data.json fetch failed: ${res.status} (${DATA_URL})`);
  const { model, series } = await res.json();
  if (!model || !series) throw new Error("data.json missing model/series");

  let fill = await fetchDailyFill(model.lastDate);
  if (!fill.length) { const s = await fetchSpot(); const d = todayISO(); if (s && d > model.lastDate) fill = [{ date: d, price: s }]; }

  const prices = series.p.slice(), dates = series.t.slice();
  for (const pt of fill) { prices.push(pt.price); dates.push(pt.date); }

  let prevRisk = model.lastRisk, runMax = model.lastATH || 0;
  for (const v of series.p) if (v > runMax) runMax = v;

  let out = { risk: prevRisk, conf: 0 }, lastDate = model.lastDate, lastPx = series.p[series.p.length - 1];
  for (let i = series.p.length; i < prices.length; i++) {
    if (prices[i] > runMax) runMax = prices[i];
    out = riskAt(model, prices, i, prevRisk, runMax);
    prevRisk = out.risk; lastDate = dates[i]; lastPx = prices[i];
  }
  return { date: lastDate, risk: out.risk, conf: out.conf, price: lastPx, live: fill.length > 0 };
}

/* ===== optional CLI: `node btc-risk-weekly.mjs` prints today's reading (no send) ===== */
if (process.argv[1] && process.argv[1].endsWith("btc-risk-weekly.mjs")) {
  getLatestRisk().then((s) => {
    const a = actionFor(s.risk);
    console.log(`[${s.date}${s.live ? " live" : " dataset"}] risk=${s.risk.toFixed(4)} conf=${s.conf} px=${Math.round(s.price)} -> ${a.kind}${a.mult ? (" " + a.mult + "x") : a.parts ? (" " + a.parts + "/15") : ""}`);
    process.exit(0);
  }).catch((e) => { console.error(e); process.exit(1); });
}
