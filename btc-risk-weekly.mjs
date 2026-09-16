// btc-risk-weekly.mjs
// =============================================================================
// SHARED RISK LIBRARY for bitcoinrisk.net.
//
// Reads the COMMITTED v3 row — the last line of series/v3.0.jsonl — and falls
// back to v2's data.json when the tape is missing or more than MAX_STALE_DAYS
// old. It no longer recomputes anything: the old path re-ran the v2 heldSubs
// blend against a live spot price, which meant the email, the site and the API
// could each publish a slightly different "current risk". There is now one
// number, and it is read.
//
// THE SCALES ARE DIFFERENT OBJECTS. v2's blend had a median of 0.370 and never
// exceeded 0.927. The v3 score is a CAUSAL PERCENTILE RANK with a median of
// 0.66: 0.80 means "more extended than 80% of history up to that morning", and
// it occurs on ~30% of days where v2's 0.80 occurred on 2.8%. Every threshold
// below was re-derived for that; none was carried over. Anything that quotes a
// number must also say which model produced it.
//
// Exports:
//   getLatestRisk()                    -> { date, risk, risk100, price, conf, live,
//                                           model, schema_version, band, fallback }
//   regimeFor(risk, lang, model)       -> { key, regime, color } bilingual
//   labelsFor(risk, lang, model)       -> alias of regimeFor, kept for callers
//   strategyBuy(strategy, risk, base)  -> { k, amount, save, share }
//   meaningLine(risk, date, lang, model) -> what the number means (rank sentence is v3-only)
//   actionFor(risk)                    -> LEGACY v2 bands; only valid on a v2 reading
//
// Env:
//   TAPE_FILE          local path to series/v3.0.jsonl (default: series/v3.0.jsonl)
//   DATA_FILE          local path to data.json (v2 fallback); else DATA_URL is fetched
//   DATA_URL           where data.json lives (default: the deployed site)
// Node 20+ (global fetch). Zero dependencies.
// NOT FINANCIAL ADVICE.
// =============================================================================

import { readFile } from "node:fs/promises";

// DATA_FILE (a local path) wins when set — the notify Action reads the data.json from its
// own checkout, so it needs no public URL. Otherwise fetch DATA_URL (the deployed site).
const DATA_FILE = process.env.DATA_FILE || "";
const DATA_URL  = process.env.DATA_URL  || "https://bitcoinrisk.net/data.json";
const TAPE_FILE = process.env.TAPE_FILE || "series/v3.0.jsonl";
/* Older than this and the v3 row is not "current" any more, so we say so and
   serve v2 rather than presenting a stale number as today's. Same constant as
   v3.js uses in the browser, deliberately. */
const MAX_STALE_DAYS = 4;

const todayISO = () => new Date().toISOString().slice(0, 10);

/* The v2 heldSubs recompute that used to live here is GONE. It re-derived the
   score from a live spot price while the site served the committed one, so the
   email could disagree with the dashboard by a couple of points on any given
   morning. Both now read a committed row. */

/* ===== LEGACY v2 bands =======================================================
   Only meaningful on a v2 reading. On the v3 rank scale `r >= 0.80` occurs on
   ~30% of days rather than 2.8%, so "sell 3/15 of your stack" would fire three
   days in ten; and `r < 0.10` \u2014 the 5x buy tier \u2014 never fired once in fifteen
   years of v2. Kept solely so the v2 fallback path keeps describing v2
   correctly. Do not call it on a v3 reading. */
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

/* ===== per-strategy DCA amount ================================================
   A continuous tilt, not a band ladder: amount = base * (1 - rank)^k.

   Why continuous. The published score is a rank, so a band ladder inherited
   from v2 sits out 81% of days on the v3 tape \u2014 that is market timing wearing a
   DCA label, and its worst 4-year window gives up 89.8% of the BTC-per-dollar
   advantage. Measured over 42 rolling 4-year windows, weekly buys, no selling:

     k = 0.5  conservative   median +10.4% vs flat DCA, worst -23.4%
     k = 1    moderate       median +17.3%,              worst -43.8%
     k = 2    aggressive     median +24.5%,              worst -68.3%

   k = 1 is the rule the model's own admission test measured at +17.1%. The
   ladder scored a higher median (+40.5%) and is still the wrong rule.

   There is NO SELL TIER. The only policy this project has measured is
   buy-more-when-low with no selling, and per the changelog a sell must be bound
   to something scale-free rather than a fixed cut on a rank. */
const TILT_K = { cons: 0.5, mod: 1, aggr: 2 };

export function strategyBuy(strategy, risk, base) {
  const b = (Number.isFinite(base) && base > 0) ? base : 100;
  const k = TILT_K[strategy] ?? TILT_K.mod;
  const r = Math.min(1, Math.max(0, Number(risk)));
  const share = Math.pow(1 - r, k);
  const amount = Math.round(share * b);
  /* At the very top of the range the tilt rounds below a dollar. That is a real
     outcome, not a "wait for risk to fall" instruction: extension is as high as
     the tape has recorded, and the rule buys nothing this period. */
  return { k, share, amount, save: amount < 1 };
}

/* ===== bilingual regime label + colour ========================================
   v3 wording is the model's OWN quintile vocabulary \u2014 the same language the
   reach and bottom gates use \u2014 because the score is a percentile and the honest
   label describes extension, not danger. Frequencies on the v3 tape are in the
   comments; they are the reason "Distribution" is gone. */
const V3_REGIMES = [
  { max: 0.20, key: "bottom", en: "Bottom quintile",   es: "Quintil inferior",       color: "#3fb950" }, // 12% of days
  { max: 0.60, key: "below",  en: "Below mid",         es: "Por debajo del medio",   color: "#46b3c9" }, // 30%
  { max: 0.80, key: "above",  en: "Above mid",         es: "Por encima del medio",   color: "#ec7a1c" }, // 28%
  { max: Infinity, key: "top", en: "Top quintile",     es: "Quintil superior",       color: "#d63d2e" }, // 30%
];

export function regimeFor(r, lang, model) {
  const es = lang === "es";
  if (model === "v2") {
    /* v2's own scale keeps v2's own words; mixing the two vocabularies is what
       this whole cutover exists to prevent. */
    const a = actionFor(r);
    if (a.kind === "buy")  return { key: "v2buy",  regime: es ? "Acumulaci\u00F3n" : "Accumulation", color: "#3fb950" };
    if (a.kind === "hold") return { key: "v2hold", regime: "Neutral", color: "#e7b53b" };
    return { key: "v2sell", regime: es ? "Distribuci\u00F3n" : "Distribution", color: "#f0883e" };
  }
  const b = V3_REGIMES.find((x) => r < x.max) || V3_REGIMES[V3_REGIMES.length - 1];
  return { key: b.key, regime: es ? b.es : b.en, color: b.color };
}

export function labelsFor(r, lang, model) { return regimeFor(r, lang, model); }

/* ===== the sentence that says what the number means ===========================
   Without this a reader who remembers v2 sees 0.82 and thinks "once a cycle".
   On the rank scale it is a top-quintile reading that occurs on ~30% of days. */
export function meaningLine(r, date, lang, model) {
  const es = lang === "es";
  /* The percentile sentence is TRUE ONLY OF v3. v2's number is a blend of
     percentiles, not a percentile \u2014 saying "more extended than 35% of its
     history" about a v2 reading would be exactly the false claim decision 18
     was raised to fix, just relocated into an email. */
  if (model === "v2") {
    return es ? `puntuaci\u00F3n combinada del modelo v2 para ${date}`
              : `the v2 model's blended score for ${date}`;
  }
  const pct = Math.round(r * 100);
  return es
    ? `m\u00E1s extendido que el ${pct} % de su historia hasta ${date}`
    : `more extended than ${pct}% of its history to ${date}`;
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

/* ===== the committed v3 row — the last line of the append-only tape =========== */
const daysSince = (iso) =>
  Math.round((Date.parse(todayISO() + "T00:00:00Z") - Date.parse(iso + "T00:00:00Z")) / 86400000);

async function readV3Row() {
  let txt;
  try { txt = await readFile(TAPE_FILE, "utf8"); }
  catch (e) { return { row: null, reason: `v3 tape unreadable at ${TAPE_FILE} (${e.code || e.message})` }; }
  const lines = txt.split("\n").filter((l) => l.trim());
  if (!lines.length) return { row: null, reason: "v3 tape is empty" };
  let row;
  try { row = JSON.parse(lines[lines.length - 1]); }
  catch { return { row: null, reason: "last line of the v3 tape is not valid JSON" }; }
  if (!row || typeof row.risk01 !== "number" || !row.asof_date) {
    return { row: null, reason: "last v3 row is missing risk01 or asof_date" };
  }
  const lag = daysSince(row.asof_date);
  if (lag > MAX_STALE_DAYS) {
    return { row: null, reason: `v3 tape is ${lag} days behind (${row.asof_date}), limit ${MAX_STALE_DAYS}` };
  }
  return { row, reason: null };
}

/* v2's own committed row: the last point of the published series. NOT a
   recompute — the old code re-ran the heldSubs blend against a live spot price,
   which is how the email, the site and the API each ended up with their own
   "current risk". */
async function readV2Row() {
  let model, series;
  if (DATA_FILE) {
    ({ model, series } = JSON.parse(await readFile(DATA_FILE, "utf8")));
  } else {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`data.json fetch failed: ${res.status} (${DATA_URL})`);
    ({ model, series } = await res.json());
  }
  if (!model || !series) throw new Error("data.json missing model/series");
  const i = series.t.length - 1;
  return { date: series.t[i], risk: series.r[i], conf: series.c[i], price: series.p[i] };
}

/* ===== PUBLIC: getLatestRisk() — the committed row, v3 first ==================
   `price` is the committed daily close that belongs to the risk number.
   `spot` is a live quote and is deliberately a SEPARATE field: price alerts
   need it, the risk number must never move with it. */
export async function getLatestRisk() {
  const { row, reason } = await readV3Row();
  const spot = await fetchSpot();

  if (row) {
    return {
      date: row.asof_date,
      risk: row.risk01,
      risk100: row.risk100,
      price: row.price_usd,
      conf: row.conf,
      spot,
      live: false,                       // the score is a committed row, never live
      model: "v3",
      schema_version: row.schema_version || "v3.0",
      band: (row.risk_lo != null && row.risk_hi != null)
        ? { lo: row.risk_lo / 100, hi: row.risk_hi / 100 } : null,
      fallback: null,
    };
  }

  const v2 = await readV2Row();
  return {
    ...v2, spot, live: false, model: "v2", schema_version: "v2",
    band: null,
    /* Say so. Serving a v2 number silently, on a scale where 0.80 means
       something else entirely, is the failure this field exists to prevent. */
    fallback: reason,
  };
}

/* ===== optional CLI: `node btc-risk-weekly.mjs` prints today's reading (no send) ===== */
if (process.argv[1] && process.argv[1].endsWith("btc-risk-weekly.mjs")) {
  getLatestRisk().then((s) => {
    const reg = regimeFor(s.risk, "en", s.model);
    const rec = strategyBuy("mod", s.risk, 200);
    console.log(`[${s.date} ${s.model}] risk=${s.risk.toFixed(4)} conf=${s.conf} `
      + `px=${Math.round(s.price)} -> ${reg.regime}; ${meaningLine(s.risk, s.date, "en", s.model)}`);
    console.log(`  moderate tilt on a $200 base: $${rec.amount}${rec.save ? " (nothing this period)" : ""}`);
    if (s.fallback) console.log(`  [FALLBACK] serving v2: ${s.fallback}`);
    process.exit(0);
  }).catch((e) => { console.error(e); process.exit(1); });
}
