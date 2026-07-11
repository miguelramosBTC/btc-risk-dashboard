// functions/api/keys.js  ->  /api/keys
//
// POST   {"email": "..."}                Issue a new free-tier key. The key is
//                                        DELIVERED BY EMAIL (Resend) — this
//                                        proves inbox ownership, since the site
//                                        has no login system. The key is stored
//                                        only as a SHA-256 hash and cannot be
//                                        shown again.
// GET    Authorization: Bearer <key>     List every key belonging to the
//                                        bearer's email (sanitized: prefix,
//                                        tier, usage — never the full key).
// DELETE Authorization: Bearer <key>     Revoke a key. ?id=<uuid> (or JSON
//                                        body {"id":...}) revokes that key if
//                                        it belongs to the same email; without
//                                        an id, revokes the presented key.
//
// Tier upgrades are NOT self-service here: 'pro'/'power' are set by Stripe in
// Phase 3 (or manually: update api_keys set tier='pro' where key_prefix='...').
//
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM,
//      SITE_URL (optional).

import { sb } from "../../lib/supabase.js";
import { CORS, json, err, sha256Hex, newApiKey, bearerFrom, findKeyByBearer, TIERS } from "../../lib/api.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_ACTIVE_KEYS_PER_EMAIL = 3;
const MAX_KEYGEN_PER_IP_DAY = 5;

export function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

/* ---------------------------- POST: issue a key --------------------------- */
export async function onRequestPost(context) {
  const { request, env } = context;
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    console.error("keys: missing supabase env");
    return err(500, "server", "Server misconfigured.");
  }

  let body;
  try { body = await request.json(); }
  catch { return err(400, "bad_json", 'Body must be JSON, e.g. {"email":"you@example.com"}.'); }
  const email = String(body?.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return err(400, "bad_email", "Provide a valid email address — your key is delivered there.");
  }

  const db = sb(env);
  const day = new Date().toISOString().slice(0, 10);

  // per-IP creation cap (reuses the api_usage table with a "keygen:" prefix)
  const ip = "keygen:" + (request.headers.get("CF-Connecting-IP") || "unknown");
  try {
    const q = `day=eq.${day}&ip=eq.${encodeURIComponent(ip)}`;
    const rows = await db.select("api_usage", `${q}&select=count&limit=1`);
    const used = rows && rows.length ? rows[0].count : 0;
    if (used >= MAX_KEYGEN_PER_IP_DAY) {
      return err(429, "rate_limited",
        `Key-creation limit reached (${MAX_KEYGEN_PER_IP_DAY}/day per IP). Try again after 00:00 UTC.`,
        {}, { "Retry-After": "3600" });
    }
    if (!rows || !rows.length) {
      try { await db.insert("api_usage", { day, ip, count: 1 }, "minimal"); } catch (e) { /* concurrent */ }
    } else {
      await db.update("api_usage", q, { count: used + 1 }, "minimal");
    }
  } catch (e) {
    console.error("keys: keygen limit (fail-open):", e.message);
  }

  // cap active keys per email
  let existing;
  try {
    existing = await db.select("api_keys",
      `email=eq.${encodeURIComponent(email)}&active=eq.true&select=id&limit=${MAX_ACTIVE_KEYS_PER_EMAIL + 1}`);
  } catch (e) {
    console.error("keys: existing check:", e.message);
    return err(502, "db", "Could not check existing keys. Try again shortly.");
  }
  if (existing && existing.length >= MAX_ACTIVE_KEYS_PER_EMAIL) {
    return err(409, "too_many_keys",
      `This email already has ${MAX_ACTIVE_KEYS_PER_EMAIL} active keys. Revoke one via DELETE /api/keys first.`);
  }

  // create (hash-only storage) …
  const { key, prefix } = newApiKey();
  const key_hash = await sha256Hex(key);
  let row;
  try {
    const ins = await db.insert("api_keys", { email, key_hash, key_prefix: prefix, tier: "free" });
    row = ins && ins[0];
    if (!row || !row.id) throw new Error("insert returned no row");
  } catch (e) {
    console.error("keys: insert:", e.message);
    return err(502, "db", "Could not create the key. Try again shortly.");
  }

  // … and deliver by email. If delivery fails, the key is revoked so an
  // undeliverable key never stays live.
  try {
    await sendKeyEmail(env, email, key);
  } catch (e) {
    console.error("keys: email:", e.message);
    try {
      await db.update("api_keys", `id=eq.${row.id}`,
        { active: false, revoked_at: new Date().toISOString() }, "minimal");
    } catch (_) {}
    return err(502, "email_failed",
      "The key could not be delivered by email, so it was not issued. Try again later.");
  }

  return json({
    ok: true, delivery: "email", email, key_prefix: prefix, tier: "free",
    note: "The full key was sent to your inbox. It is stored only as a hash and cannot be shown again.",
  });
}

/* ----------------------------- GET: list keys ----------------------------- */
export async function onRequestGet(context) {
  const { request, env } = context;
  const bearer = bearerFrom(request);
  if (!bearer) {
    return err(401, "missing_key",
      "Send your API key as: Authorization: Bearer <key>. Need one? POST /api/keys with your email.");
  }
  let found;
  try { found = await findKeyByBearer(env, bearer); }
  catch (e) {
    console.error("keys: lookup:", e.message);
    return err(503, "auth_unavailable", "Could not validate the API key. Try again shortly.");
  }
  if (found.notFound) return err(401, "invalid_key", "Unknown or revoked API key.");

  let rows;
  try {
    rows = await sb(env).select("api_keys",
      `email=eq.${encodeURIComponent(found.row.email)}` +
      `&select=id,key_prefix,tier,active,created_at,last_used_at,revoked_at,request_count` +
      `&order=created_at.desc`);
  } catch (e) {
    console.error("keys: list:", e.message);
    return err(502, "db", "Could not list keys. Try again shortly.");
  }
  return json({ email: found.row.email, keys: rows || [] });
}

/* ---------------------------- DELETE: revoke ------------------------------ */
export async function onRequestDelete(context) {
  const { request, env } = context;
  const bearer = bearerFrom(request);
  if (!bearer) {
    return err(401, "missing_key", "Send your API key as: Authorization: Bearer <key>.");
  }
  let found;
  try { found = await findKeyByBearer(env, bearer); }
  catch (e) {
    console.error("keys: lookup:", e.message);
    return err(503, "auth_unavailable", "Could not validate the API key. Try again shortly.");
  }
  if (found.notFound) return err(401, "invalid_key", "Unknown or revoked API key.");

  let id = new URL(request.url).searchParams.get("id") || "";
  if (!id) { try { const b = await request.json(); id = String(b?.id || ""); } catch (_) {} }

  const db = sb(env);
  let target = found.row;
  if (id && id !== found.row.id) {
    if (!UUID_RE.test(id)) return err(400, "bad_id", "id must be the key's UUID (see GET /api/keys).");
    let rows;
    try { rows = await db.select("api_keys", `id=eq.${encodeURIComponent(id)}&select=*&limit=1`); }
    catch (e) {
      console.error("keys: target lookup:", e.message);
      return err(502, "db", "Could not look up that key. Try again shortly.");
    }
    const r = rows && rows[0];
    if (!r || r.email !== found.row.email) {
      return err(404, "not_found", "No key with that id belongs to this email.");
    }
    target = r;
  }

  if (target.active !== false) {
    try {
      await db.update("api_keys", `id=eq.${target.id}`,
        { active: false, revoked_at: new Date().toISOString() }, "minimal");
    } catch (e) {
      console.error("keys: revoke:", e.message);
      return err(502, "db", "Could not revoke the key. Try again shortly.");
    }
  }
  return json({ ok: true, revoked: target.id, key_prefix: target.key_prefix });
}

/* ------------------------- email delivery (Resend) ------------------------ */
async function sendKeyEmail(env, email, key) {
  const { RESEND_API_KEY, EMAIL_FROM } = env;
  if (!RESEND_API_KEY || !EMAIL_FROM) throw new Error("missing RESEND_API_KEY / EMAIL_FROM");
  const site = (env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  const f = TIERS.free;
  const html = `<!doctype html><html><body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:560px;margin:0 auto;padding:40px 22px">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk</div>
    <h1 style="margin:14px 0;font-size:22px;color:#f7931a">Tu API key / Your API key</h1>
    <p style="font-size:14px;line-height:1.6;color:#cfd6e0">Guarda esta clave en un lugar seguro: <b>no se puede volver a mostrar</b> (solo almacenamos un hash).<br>
    Keep this key safe: <b>it cannot be shown again</b> (we only store a hash).</p>
    <div style="background:#131a24;border:1px solid #232c3a;border-radius:9px;padding:14px;font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;word-break:break-all;color:#e8edf4">${key}</div>
    <p style="font-size:14px;line-height:1.6;color:#cfd6e0;margin-top:18px"><b>Uso / Usage:</b></p>
    <div style="background:#131a24;border:1px solid #232c3a;border-radius:9px;padding:14px;font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;word-break:break-all;color:#9fb0c3">curl -H "Authorization: Bearer ${key.slice(0, 12)}..." ${site}/api/risk/current</div>
    <p style="font-size:13px;line-height:1.7;color:#8b97a7;margin-top:18px">
    Plan gratuito / Free tier: ${f.per_day} peticiones al d\u00EDa / requests per day \u00B7 hist\u00F3rico de los \u00FAltimos ${f.window_days} d\u00EDas / last ${f.window_days} days of history.<br>
    Gestiona tus claves con / Manage your keys with GET &amp; DELETE ${site}/api/keys.<br>
    Si no solicitaste esta clave, ignora este correo. / If you didn't request this key, ignore this email.<br>
    Datos con fines educativos; no es asesoramiento financiero. / Educational data; not financial advice.</p>
  </div></body></html>`;
  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: EMAIL_FROM, to: email, subject: "Tu API key / Your API key \u00B7 Bitcoin Risk", html }),
  });
  if (!resp.ok) throw new Error(`Resend ${resp.status}: ${await resp.text()}`);
}
