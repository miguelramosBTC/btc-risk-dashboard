// functions/api/export.js  ->  GET /api/export?type=csv|json&from=YYYY-MM-DD&to=YYYY-MM-DD
//
// Serves the published daily series (date, price_usd, risk, confidence) from the same
// data.json the dashboard is built from, so API numbers always match the site exactly.
//
// Tiers (Phase 1 — no accounts yet):
//   free (default)      : window clamped to the last 90 days, 50 requests/day per IP
//   pro (stopgap)       : header `X-Api-Key` matching one of env.API_PRO_KEYS
//                         (comma-separated) unlocks the full history and skips the
//                         daily cap. Real key issuance arrives in Phase 2.
//
// Honest schema note: data.json does not (yet) contain per-day n_* component columns —
// the pipeline only publishes the latest snapshot (MODEL.heldSubs). This endpoint
// therefore returns the four daily columns plus `components_latest`, and says so in
// `meta.components_note` instead of pretending per-day components exist.
//
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (rate limiting; endpoint fails open
//      if missing/unreachable), API_PRO_KEYS (optional).
// DB : table api_usage(day date, ip text, count int, primary key(day, ip)).

import { sb } from "../../lib/supabase.js";

const FREE_DAILY_LIMIT = 50;
const FREE_WINDOW_DAYS = 90;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "X-Api-Key, Content-Type",
};
const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });

export function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

/* mirrors the model / frontend exactly */
const sig = (x, c, b) => 1 / (1 + Math.exp(-(x - c) / b));
const clip = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;

function componentsLatest(model, series) {
  const p = series.p, lastPx = p[p.length - 1];
  const sma = mean(p.slice(-model.smaMayer));
  const out = {
    mayer: Number(clip(sig(lastPx / sma, model.params.mayer[0], model.params.mayer[1]), 0, 1).toFixed(6)),
    ath: Number(clip(Math.pow(Math.min(lastPx / model.lastATH, 1), model.athExp), 0, 1).toFixed(6)),
  };
  for (const k in model.heldSubs) out[k] = model.heldSubs[k];
  return out;
}

/* 50/day/IP via Supabase. Fails OPEN (never blocks the API on a DB hiccup). */
async function checkRateLimit(env, ip) {
  try {
    if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) return { ok: true, remaining: null };
    const db = sb(env);
    const day = new Date().toISOString().slice(0, 10);
    const q = `day=eq.${day}&ip=eq.${encodeURIComponent(ip)}`;
    const rows = await db.select("api_usage", `${q}&select=count&limit=1`);
    if (!rows || !rows.length) {
      try { await db.insert("api_usage", { day, ip, count: 1 }, "minimal"); } catch (e) { /* concurrent first insert */ }
      return { ok: true, remaining: FREE_DAILY_LIMIT - 1 };
    }
    const used = rows[0].count + 1;
    if (used > FREE_DAILY_LIMIT) return { ok: false, remaining: 0 };
    await db.update("api_usage", q, { count: used }, "minimal");
    return { ok: true, remaining: FREE_DAILY_LIMIT - used };
  } catch (e) {
    console.error("export rate-limit (fail-open):", e.message);
    return { ok: true, remaining: null };
  }
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  // ---- params ------------------------------------------------------------
  const type = (url.searchParams.get("type") || "json").toLowerCase();
  if (type !== "json" && type !== "csv") return json({ error: "type must be csv or json" }, 400);
  let from = url.searchParams.get("from") || "";
  let to = url.searchParams.get("to") || "";
  if (from && !DATE_RE.test(from)) return json({ error: "from must be YYYY-MM-DD" }, 400);
  if (to && !DATE_RE.test(to)) return json({ error: "to must be YYYY-MM-DD" }, 400);
  if (from && to && from > to) return json({ error: "from must be <= to" }, 400);

  // ---- tier --------------------------------------------------------------
  const keys = String(env.API_PRO_KEYS || "").split(",").map((s) => s.trim()).filter(Boolean);
  const given = request.headers.get("X-Api-Key") || url.searchParams.get("key") || "";
  const pro = keys.length > 0 && keys.includes(given);

  // ---- rate limit (free tier only) ----------------------------------------
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  let remaining = null;
  if (!pro) {
    const rl = await checkRateLimit(env, ip);
    remaining = rl.remaining;
    if (!rl.ok) {
      return json(
        { error: "rate_limited", detail: `Free tier: ${FREE_DAILY_LIMIT} requests/day. Resets 00:00 UTC.` },
        429, { "Retry-After": "3600", "X-RateLimit-Limit": String(FREE_DAILY_LIMIT), "X-RateLimit-Remaining": "0" },
      );
    }
  }

  // ---- load the same data.json the site serves ----------------------------
  let model, series;
  try {
    const res = await env.ASSETS.fetch(new URL("/data.json", request.url));
    if (!res.ok) throw new Error(`data.json ${res.status}`);
    ({ model, series } = await res.json());
    if (!model || !series) throw new Error("data.json missing model/series");
  } catch (e) {
    console.error("export data:", e.message);
    return json({ error: "data_unavailable" }, 503);
  }

  // ---- resolve + clamp the window -----------------------------------------
  const lastDate = series.t[series.t.length - 1];
  if (!to || to > lastDate) to = lastDate;
  if (!from) from = series.t[0];
  let clamped = false;
  if (!pro) {
    const floor = new Date(Date.parse(lastDate + "T00:00:00Z") - FREE_WINDOW_DAYS * 86400000)
      .toISOString().slice(0, 10);
    if (from < floor) { from = floor; clamped = true; }
  }

  const lo = series.t.findIndex((d) => d >= from);
  if (lo === -1 || series.t[lo] > to) return json({ error: "empty_range", from, to }, 404);
  let hi = lo; while (hi + 1 < series.t.length && series.t[hi + 1] <= to) hi++;

  const rlHeaders = remaining === null ? {} : {
    "X-RateLimit-Limit": String(FREE_DAILY_LIMIT), "X-RateLimit-Remaining": String(remaining),
  };
  const stamp = new Date().toISOString().slice(0, 10);

  // ---- CSV ----------------------------------------------------------------
  if (type === "csv") {
    let out = "date,price_usd,risk,confidence\n";
    for (let i = lo; i <= hi; i++) out += `${series.t[i]},${series.p[i]},${series.r[i]},${series.c[i]}\n`;
    return new Response(out, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="btc-risk-chart-${stamp}.csv"`,
        "Cache-Control": "public, max-age=300",
        ...CORS, ...rlHeaders,
      },
    });
  }

  // ---- JSON ---------------------------------------------------------------
  const body = {
    meta: {
      source: "bitcoinrisk.net",
      generated_at: new Date().toISOString(),
      tier: pro ? "pro" : "free",
      from: series.t[lo], to: series.t[hi], rows: hi - lo + 1,
      free_window_days: pro ? null : FREE_WINDOW_DAYS,
      window_clamped: clamped,
      components_note: "Per-day component columns are not yet published; components_latest is the current snapshot (mayer/ath derived from published prices with the model's own formulas).",
      disclaimer: "Educational data, not financial advice. Schema may change without notice.",
    },
    model: {
      weights: model.weights, params: model.params, athExp: model.athExp,
      emaSpan: model.emaSpan, smaMayer: model.smaMayer, lastDate: model.lastDate,
    },
    components_latest: { asOf: model.lastDate, values: componentsLatest(model, series) },
    series: {
      date: series.t.slice(lo, hi + 1),
      price_usd: series.p.slice(lo, hi + 1),
      risk: series.r.slice(lo, hi + 1),
      confidence: series.c.slice(lo, hi + 1),
    },
  };
  return json(body, 200, { "Cache-Control": "public, max-age=300", ...rlHeaders });
}
