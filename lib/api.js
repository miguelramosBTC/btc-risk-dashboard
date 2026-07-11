// lib/api.js
// Shared plumbing for the public API (Phase 2): tier definitions, key hashing,
// bearer authentication, rate limiting, and data.json loading.
//
// Identity model: the site has no login system, so keys are anchored to a
// verified EMAIL (the key is delivered by email, which proves inbox ownership —
// same philosophy as the double opt-in subscriptions). The api_keys table keeps
// nullable user_id / stripe_customer_id columns so Supabase Auth or Stripe
// (Phase 3) can attach later without a migration.
//
// Keys are stored ONLY as SHA-256 hashes (key_hash) plus a display prefix.
// A database leak therefore does not leak usable keys.

import { sb } from "./supabase.js";

/* Tier limits — single source of truth; tune freely. window_days null = full
   history. `components` gates per-day component fields on /historical. */
export const TIERS = {
  free:  { per_day: 50,    window_days: 90,   components: false },
  pro:   { per_day: 5000,  window_days: null, components: true  },
  power: { per_day: 50000, window_days: null, components: true  },
};

export const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, X-Api-Key, Content-Type",
};

export const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });

export const err = (status, code, message, bodyExtra = {}, headers = {}) =>
  json({ error: code, message, ...bodyExtra }, status, headers);

export async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function newApiKey() {
  const b = new Uint8Array(24);
  crypto.getRandomValues(b);
  const key = "brk_" + [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return { key, prefix: key.slice(0, 12) }; // "brk_" + 8 hex chars — safe to display
}

export function bearerFrom(request) {
  const h = request.headers.get("Authorization") || "";
  const m = h.match(/^Bearer\s+(\S+)$/i);
  if (m) return m[1];
  return request.headers.get("X-Api-Key") || null; // convenience fallback
}

const today = () => new Date().toISOString().slice(0, 10);

/* Resolve an ACTIVE key row from a raw bearer string.
   Returns { row } or { notFound: true }; throws on backend failure. */
export async function findKeyByBearer(env, bearer) {
  const hash = await sha256Hex(bearer);
  const rows = await sb(env).select(
    "api_keys",
    `key_hash=eq.${hash}&active=eq.true&select=*&limit=1`,
  );
  return rows && rows.length ? { row: rows[0] } : { notFound: true };
}

/* One-stop gate for data endpoints: authenticates (key or anonymous) and
   consumes one unit of daily quota.
   Returns { ok:true, tier, limits, keyRow|null, headers }
        or { ok:false, response }.
   Policy: key VALIDATION failures fail closed (503); usage ACCOUNTING
   failures fail open (availability over strictness). Anonymous callers get
   free-tier limits per IP via the api_usage table from Phase 1. */
export async function gate(context) {
  const { request, env } = context;
  const bearer = bearerFrom(request);

  if (bearer) {
    let found;
    try {
      found = await findKeyByBearer(env, bearer);
    } catch (e) {
      console.error("gate: key lookup failed:", e.message);
      return { ok: false, response: err(503, "auth_unavailable",
        "Could not validate the API key (auth backend unavailable). Try again shortly.") };
    }
    if (found.notFound) {
      return { ok: false, response: err(401, "invalid_key",
        "Unknown or revoked API key. Request a free key via POST /api/keys with {\"email\":\"you@example.com\"}.") };
    }
    const row = found.row;
    const limits = TIERS[row.tier] || TIERS.free;
    const d = today();
    const used = row.day === d ? (row.day_count || 0) : 0;
    if (used + 1 > limits.per_day) {
      return { ok: false, response: err(429, "rate_limited",
        `Daily limit reached for the '${row.tier}' tier (${limits.per_day} requests/day). Resets 00:00 UTC.`,
        { tier: row.tier, limit: limits.per_day },
        { "Retry-After": "3600", "X-RateLimit-Limit": String(limits.per_day), "X-RateLimit-Remaining": "0" }) };
    }
    try {
      await sb(env).update("api_keys", `id=eq.${row.id}`, {
        day: d,
        day_count: used + 1,
        request_count: (row.request_count || 0) + 1,
        last_used_at: new Date().toISOString(),
      }, "minimal");
    } catch (e) {
      console.error("gate: usage update failed (fail-open):", e.message);
    }
    const remaining = limits.per_day - (used + 1);
    return { ok: true, tier: row.tier, limits, keyRow: row,
      headers: { "X-RateLimit-Limit": String(limits.per_day), "X-RateLimit-Remaining": String(remaining) } };
  }

  /* anonymous — free-tier limits per IP (same api_usage table as /api/export) */
  const limits = TIERS.free;
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  let remaining = null;
  try {
    if (env.SUPABASE_URL && env.SUPABASE_SERVICE_ROLE_KEY) {
      const db = sb(env), d = today();
      const q = `day=eq.${d}&ip=eq.${encodeURIComponent(ip)}`;
      const rows = await db.select("api_usage", `${q}&select=count&limit=1`);
      if (!rows || !rows.length) {
        try { await db.insert("api_usage", { day: d, ip, count: 1 }, "minimal"); } catch (e) { /* concurrent first insert */ }
        remaining = limits.per_day - 1;
      } else {
        const used = rows[0].count + 1;
        if (used > limits.per_day) {
          return { ok: false, response: err(429, "rate_limited",
            `Anonymous limit reached (${limits.per_day} requests/day per IP). Get a free API key via POST /api/keys, or retry after 00:00 UTC.`,
            { limit: limits.per_day },
            { "Retry-After": "3600", "X-RateLimit-Limit": String(limits.per_day), "X-RateLimit-Remaining": "0" }) };
        }
        await db.update("api_usage", q, { count: used }, "minimal");
        remaining = limits.per_day - used;
      }
    }
  } catch (e) {
    console.error("gate: anon rate-limit (fail-open):", e.message);
  }
  const headers = remaining === null ? {} :
    { "X-RateLimit-Limit": String(limits.per_day), "X-RateLimit-Remaining": String(remaining) };
  return { ok: true, tier: "free", limits, keyRow: null, headers };
}

/* Load the same data.json the dashboard is built from, so API numbers always
   match the site exactly. Throws on failure. */
export async function loadData(context) {
  const res = await context.env.ASSETS.fetch(new URL("/data.json", context.request.url));
  if (!res.ok) throw new Error(`data.json ${res.status}`);
  const { model, series } = await res.json();
  if (!model || !series) throw new Error("data.json missing model/series");
  return { model, series };
}

/* Latest normalized components: the 9 published on-chain sub-scores plus
   mayer/ath recomputed from published prices with the model's own formulas
   (identical to the frontend export and /api/export). */
export function componentsLatest(model, series) {
  const sig = (x, c, b) => 1 / (1 + Math.exp(-(x - c) / b));
  const clip = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const p = series.p, lastPx = p[p.length - 1];
  const sma = p.slice(-model.smaMayer).reduce((s, v) => s + v, 0) / Math.min(p.length, model.smaMayer);
  const out = {
    mayer: Number(clip(sig(lastPx / sma, model.params.mayer[0], model.params.mayer[1]), 0, 1).toFixed(6)),
    ath: Number(clip(Math.pow(Math.min(lastPx / model.lastATH, 1), model.athExp), 0, 1).toFixed(6)),
  };
  for (const k in model.heldSubs) out[k] = model.heldSubs[k];
  return out;
}
