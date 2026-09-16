// functions/api/risk/[[route]].js  ->  /api/risk, /api/risk/current,
//                                      /api/risk/historical, /api/risk/model
//
// (Cloudflare Pages uses file-based routing, so a single "risk.js" cannot
// serve sub-paths — this catch-all is the Pages equivalent of that file.)
//
// All endpoints accept `Authorization: Bearer <api_key>` (or X-Api-Key).
// Anonymous callers are allowed at free-tier limits, metered per IP; keyed
// callers are metered on their api_keys row (tier-based daily limits).
// Data is read from the deployed data.json, so API numbers always match the
// dashboard exactly.
//
// GET /api/risk            endpoint index (free, unmetered)
// GET /api/risk/current    latest risk, price, confidence + normalized components
// GET /api/risk/historical time series; params: from, to, fields, limit, offset
// GET /api/risk/model      current model configuration
//
// Honest data note: per-day component series (mvrv, puell, …) only exist if
// the pipeline publishes `series.n` in data.json. Until then /historical
// serves risk, price, conf — and says so — while the latest component
// snapshot is always available on /current.

import { CORS, json, err, gate, loadData, loadV3, componentsLatest, TIERS } from "../../../lib/api.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DEFAULT_LIMIT = 1000, MAX_LIMIT = 5000;

const BASE_FIELDS = { risk: "r", price: "p", conf: "c" };
const ALIASES = { price_usd: "price", confidence: "conf", c: "conf", r: "risk", p: "price" };
const COMPONENT_FIELDS = ["mayer", "ath", "mvrv", "mvrvz", "sip", "term", "puell", "mctc", "mctcmod", "rhodl", "fees"];

const DISCLAIMER = "Educational data, not financial advice.";

export function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

export async function onRequestGet(context) {
  const route = (context.params.route || []).join("/");

  if (route === "") return endpointIndex();
  if (!["current", "historical", "model"].includes(route)) {
    return err(404, "not_found", `Unknown endpoint '/api/risk/${route}'.`,
      { endpoints: ["/api/risk/current", "/api/risk/historical", "/api/risk/model"] });
  }

  const g = await gate(context);
  if (!g.ok) return g.response;

  let data;
  try { data = await loadData(context); }
  catch (e) {
    console.error("risk: data:", e.message);
    return err(503, "data_unavailable", "The published dataset could not be loaded. Try again shortly.");
  }

  /* v3 first. When the tape is missing or stale this returns row:null with a
     reason, and every payload below says which model served it and why. */
  const v3 = await loadV3(context);

  if (route === "current") return current(data, v3, g);
  if (route === "model") return modelInfo(data, v3, g);
  return historical(context, data, g);
}

/* ------------------------------- endpoints -------------------------------- */

function endpointIndex() {
  const t = {};
  for (const k in TIERS) {
    t[k] = {
      requests_per_day: TIERS[k].per_day,
      history: TIERS[k].window_days ? `last ${TIERS[k].window_days} days` : "full",
      per_day_components: TIERS[k].components,
    };
  }
  return json({
    endpoints: {
      "GET /api/risk/current": "latest risk, price, confidence + normalized components",
      "GET /api/risk/historical": "time series; params: from, to, fields, limit, offset",
      "GET /api/risk/model": "current model configuration",
      "POST /api/keys": 'get a free API key: {"email":"you@example.com"} (delivered by email)',
      "GET|DELETE /api/keys": "list / revoke your keys (Authorization: Bearer <key>)",
    },
    auth: "Authorization: Bearer <key> — anonymous access allowed at free-tier limits (per IP).",
    tiers: t,
    disclaimer: DISCLAIMER,
  });
}

/* The scale note travels with every reading. Two models with a 0-1 score and
   different meanings is exactly how a consumer ends up comparing them. */
const V3_SCALE_NOTE =
  "risk is a CAUSAL PERCENTILE RANK: 0.80 means more extended than 80% of history "
  + "up to that morning, and occurs on ~30% of days. It is not a probability and not "
  + "a forecast. Do not compare it with a v2 number.";
const V2_SCALE_NOTE =
  "risk is v2's blended score, NOT a percentile. Its median is 0.370 and it never "
  + "exceeded 0.927, so its thresholds are not comparable with v3's.";

function current(data, v3, g) {
  const { model, series } = data;

  if (v3.row) {
    const r = v3.row;
    return json({
      as_of: r.asof_date,
      updated: "daily, ~06:05 UTC",
      schema_version: v3.schema_version,
      model: "v3",
      risk: r.risk01,
      risk100: r.risk100,
      price_usd: r.price_usd,
      confidence: r.conf,
      band: (r.risk_lo != null && r.risk_hi != null)
        ? { lo: r.risk_lo / 100, hi: r.risk_hi / 100,
            note: "image of the family-disagreement interval; asymmetric because the rank map is nonlinear" }
        : null,
      families: {
        as_of: r.asof_date,
        values: { V: r.V, G: r.G, T: r.T, S: r.S },
        weights: { V: 0.31, G: 0.24, T: 0.15, S: 0.20 },
        active_weight: r.active_weight, retired_weight: r.retired_weight,
        n_live: r.n_live, stale: r.stale,
        published_but_not_voting: { mctc_mod: r.mctc_mod ?? null },
        note: "read from the committed row; nothing is recomputed per request.",
      },
      provenance: { input_hash: r.input_hash, git_sha: r.git_sha },
      meta: { tier: g.tier, source: "bitcoinrisk.net", scale: V3_SCALE_NOTE, disclaimer: DISCLAIMER },
    }, 200, g.headers);
  }

  const i = series.t.length - 1;
  return json({
    as_of: series.t[i],
    updated: "daily, ~06:00 UTC",
    schema_version: "v2",
    model: "v2",
    fallback: { from: "v3", reason: v3.reason,
      note: "serving v2 because the v3 tape is unavailable or stale; the two scales are not comparable." },
    risk: series.r[i],
    price_usd: series.p[i],
    confidence: series.c[i],
    components: {
      as_of: model.lastDate,
      values: componentsLatest(model, series),
      derived: ["mayer", "ath"],
      note: "mayer/ath are recomputed from published prices with the model's own formulas; the rest are the pipeline's published snapshot.",
    },
    meta: { tier: g.tier, source: "bitcoinrisk.net", scale: V2_SCALE_NOTE, disclaimer: DISCLAIMER },
  }, 200, g.headers);
}
function modelInfo(data, v3, g) {
  const { model } = data;

  if (v3.row) {
    const r = v3.row;
    return json({
      schema_version: v3.schema_version,
      model: "v3",
      constitution: {
        families: {
          V: { weight: 0.31, raw: "CapMVRVCur (MVRV)", map: "0.65*F_exp + 0.35*F_4y" },
          G: { weight: 0.24, raw: "log P - (a + b*log d), d = days since 2009-01-03",
               map: "0.70*F_exp + 0.30*F_4y",
               fit: { a: r.a, b: r.b, fit_asof: r.fit_asof, n_fit: r.n_fit,
                      note: "Huber, delta 1.345, fixed 90-day refit-and-hold; never refitted because price moved" } },
          T: { weight: 0.15, raw: "P / SMA200(P) (Mayer)", map: "F_exp" },
          S: { weight: 0.20, raw: "sigma30, sigma365, kappa = sigma30/sigma365",
               map: "0.40*F_exp(sigma30) + 0.60*F_exp(fragility), fragility = V*(1 - F_exp(kappa))" },
        },
        published_but_not_voting: {
          M: { weight: 0.00, raw: "mctc_mod", note: "demoted 2026-09-11; its 0.10 was retired, not redistributed" },
          F: { weight: 0.00, note: "dark: no free public realized-profit/loss series with a fallback exists" },
        },
        active_weight: r.active_weight,
        retired_weight: r.retired_weight,
        maps: "empirical CDFs only; F_exp expanding from 2010-07-18, F_4y on a trailing 1461-day window",
        nmin: { expanding: 400, four_year: 200, growth_fit: 1200 },
        combiner: "sum(w_i * s_i) / sum(w_live), then EMA span 8 (alpha = 2/9)",
        output: "risk = F_exp(EMA(blend)) — the causal RANK of the smoothed blend, not the blend",
        inputs: ["PriceUSD", "CapMVRVCur", "CapMrktCurUSD", "SplyCur", "IssTotUSD", "IssTotNtv", "FeeTotNtv"],
      },
      limits: {
        structure: "one dominant factor plus a volatility modifier, NOT multifactor: effective rank 1.59 of 4, 78% of variance in one component",
        not_a_forecast: "conditional forward-outcome skill measured at -0.131 (worse than the base rate), reliability inverted, realised outcomes flat across rank bands",
        tape: "append-only; expanding CDFs mean a recompute would move past ranks, so a committed row is frozen",
        holdout: "the 2025-26 window is design-contaminated, not unseen",
      },
      meta: { tier: g.tier, source: "bitcoinrisk.net", scale: V3_SCALE_NOTE, disclaimer: DISCLAIMER },
    }, 200, g.headers);
  }

  return json({
    schema_version: "v2",
    model: "v2",
    fallback: { from: "v3", reason: v3.reason },
    v2_model: {
      weights: model.weights, params: model.params, athExp: model.athExp,
      emaSpan: model.emaSpan, smaMayer: model.smaMayer,
      lastDate: model.lastDate, lastRisk: model.lastRisk, lastATH: model.lastATH,
      heldSubs: model.heldSubs,
    },
    notes: {
      params: "Per-signal [center, scale] for v2's own mapping.",
      heldSubs: "Latest normalized on-chain sub-scores as published by the v2 pipeline.",
    },
    meta: { tier: g.tier, source: "bitcoinrisk.net", scale: V2_SCALE_NOTE, disclaimer: DISCLAIMER },
  }, 200, g.headers);
}
function historical(context, data, g) {
  const { series } = data;
  const url = new URL(context.request.url);

  /* ---- dates ---- */
  let from = url.searchParams.get("from") || "";
  let to = url.searchParams.get("to") || "";
  if (from && !DATE_RE.test(from)) return err(400, "bad_param", "from must be YYYY-MM-DD.");
  if (to && !DATE_RE.test(to)) return err(400, "bad_param", "to must be YYYY-MM-DD.");
  if (from && to && from > to) return err(400, "bad_param", "from must be <= to.");

  /* ---- limit / offset ---- */
  const limRaw = url.searchParams.get("limit"), offRaw = url.searchParams.get("offset");
  const limit = limRaw === null ? DEFAULT_LIMIT : parseInt(limRaw, 10);
  const offset = offRaw === null ? 0 : parseInt(offRaw, 10);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    return err(400, "bad_param", `limit must be an integer between 1 and ${MAX_LIMIT}.`);
  }
  if (!Number.isInteger(offset) || offset < 0) {
    return err(400, "bad_param", "offset must be an integer >= 0.");
  }

  /* ---- fields ---- */
  const raw = (url.searchParams.get("fields") || "risk,price,conf")
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  const fields = [];
  for (let f of raw) {
    f = ALIASES[f] || f;
    if (!(f in BASE_FIELDS) && !COMPONENT_FIELDS.includes(f)) {
      return err(400, "bad_field", `Unknown field '${f}'.`, {
        available_base: Object.keys(BASE_FIELDS),
        available_components: COMPONENT_FIELDS,
        note: "Component fields require the pro or power tier.",
      });
    }
    if (!fields.includes(f)) fields.push(f);
  }
  const compReq = fields.filter((f) => COMPONENT_FIELDS.includes(f));
  if (compReq.length && !g.limits.components) {
    return err(403, "upgrade_required",
      `Per-day component fields (${compReq.join(", ")}) require the pro or power tier. ` +
      "The latest component snapshot is free at /api/risk/current.",
      { your_tier: g.tier });
  }
  if (compReq.length && !series.n) {
    return err(501, "not_yet_published",
      "Per-day component series are not yet published by the data pipeline; historically available fields are risk, price, conf. " +
      "The latest component snapshot is at /api/risk/current.");
  }
  const missing = compReq.filter((f) => !series.n || !series.n[f]);
  if (compReq.length && missing.length) {
    return err(501, "not_yet_published",
      `These component series are not in the published dataset: ${missing.join(", ")}.`);
  }

  /* ---- window (tier clamp) ---- */
  const lastDate = series.t[series.t.length - 1];
  if (!to || to > lastDate) to = lastDate;
  if (!from) from = series.t[0];
  let clamped = false;
  if (g.limits.window_days) {
    const floor = new Date(Date.parse(lastDate + "T00:00:00Z") - g.limits.window_days * 86400000)
      .toISOString().slice(0, 10);
    if (from < floor) { from = floor; clamped = true; }
  }

  const lo = series.t.findIndex((d) => d >= from);
  if (lo === -1 || series.t[lo] > to) {
    return err(404, "empty_range", `No data between ${from} and ${to}.`,
      { available: { first: series.t[0], last: lastDate } });
  }
  let hi = lo;
  while (hi + 1 < series.t.length && series.t[hi + 1] <= to) hi++;

  /* ---- pagination within the range ---- */
  const total = hi - lo + 1;
  if (offset >= total) {
    return err(400, "bad_param", `offset ${offset} is beyond the ${total} rows in this range.`);
  }
  const a = lo + offset;
  const b = Math.min(hi, a + limit - 1);
  const next = b < hi ? offset + limit : null;

  const out = { date: series.t.slice(a, b + 1) };
  for (const f of fields) {
    out[f] = f in BASE_FIELDS
      ? series[BASE_FIELDS[f]].slice(a, b + 1)
      : series.n[f].slice(a, b + 1);
  }

  return json({
    meta: {
      from: series.t[a], to: series.t[b],
      rows: b - a + 1, total_rows_in_range: total,
      limit, offset, next_offset: next,
      fields, tier: g.tier,
      window_clamped: clamped,
      free_window_days: g.limits.window_days,
      /* This endpoint still serves the v2 series: the v3 tape is append-only and
         is not founded yet, and its published window carries only 90 days, so it
         cannot answer a full-history query. Labelled rather than left ambiguous
         — when /current is serving v3, these two endpoints are on DIFFERENT
         SCALES and must not be joined, plotted together or diffed. */
      schema_version: "v2",
      model: "v2",
      scale: V2_SCALE_NOTE,
      warning: "v2 series. If /api/risk/current reports model v3, its number is on a different scale and is not comparable with these rows.",
      source: "bitcoinrisk.net", disclaimer: DISCLAIMER,
    },
    series: out,
  }, 200, { "Cache-Control": "public, max-age=300", ...g.headers });
}
