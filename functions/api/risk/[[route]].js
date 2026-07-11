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

import { CORS, json, err, gate, loadData, componentsLatest, TIERS } from "../../../lib/api.js";

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

  if (route === "current") return current(data, g);
  if (route === "model") return modelInfo(data, g);
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

function current(data, g) {
  const { model, series } = data;
  const i = series.t.length - 1;
  return json({
    as_of: series.t[i],
    updated: "daily, ~06:00 UTC",
    risk: series.r[i],
    price_usd: series.p[i],
    confidence: series.c[i],
    components: {
      as_of: model.lastDate,
      values: componentsLatest(model, series),
      derived: ["mayer", "ath"],
      note: "mayer/ath are recomputed from published prices with the model's own formulas; the rest are the pipeline's published snapshot.",
    },
    meta: { tier: g.tier, source: "bitcoinrisk.net", disclaimer: DISCLAIMER },
  }, 200, g.headers);
}

function modelInfo(data, g) {
  const { model } = data;
  return json({
    model: {
      weights: model.weights, params: model.params, athExp: model.athExp,
      emaSpan: model.emaSpan, smaMayer: model.smaMayer,
      lastDate: model.lastDate, lastRisk: model.lastRisk, lastATH: model.lastATH,
      heldSubs: model.heldSubs,
    },
    notes: {
      params: "Per-signal logistic [center, scale]; sig(x)=1/(1+e^-((x-c)/b)).",
      heldSubs: "Latest normalized on-chain sub-scores, held for the live point.",
    },
    meta: { tier: g.tier, source: "bitcoinrisk.net", disclaimer: DISCLAIMER },
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
      source: "bitcoinrisk.net", disclaimer: DISCLAIMER,
    },
    series: out,
  }, 200, { "Cache-Control": "public, max-age=300", ...g.headers });
}
