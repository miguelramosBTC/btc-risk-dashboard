// netlify/functions/subscribe.mjs  —  POST /.netlify/functions/subscribe
// Body: { email, mode, settings }  (from the dashboard's notification panel)
//
// Double opt-in with Supabase:
//   new email      -> insert active:false + token, email a confirm link
//   pending email  -> update prefs, re-send the confirm link
//   confirmed email -> just update prefs (no re-confirm needed)
//
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM, SITE_URL
// Netlify Functions v2. Uses the SERVICE ROLE key (server-only; never expose it).

import { createClient } from "@supabase/supabase-js";

const RESEND_API = "https://api.resend.com";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MODES = new Set(["time", "price", "strategy"]);

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

function confirmHtml(siteUrl, confirmUrl) {
  return `<!doctype html><html><body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:520px;margin:0 auto;padding:30px 22px">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk</div>
    <h1 style="margin:14px 0 8px;font-size:22px;color:#f7931a">Confirma tu suscripci\u00F3n / Confirm your subscription</h1>
    <p style="font-size:15px;line-height:1.6;color:#cfd6e0;margin:0 0 6px">Pulsa el bot\u00F3n para activar tus avisos de riesgo de Bitcoin.</p>
    <p style="font-size:15px;line-height:1.6;color:#cfd6e0;margin:0 0 22px">Click the button to activate your Bitcoin Risk alerts.</p>
    <a href="${confirmUrl}" style="display:inline-block;padding:13px 26px;background:#f7931a;color:#0a0d13;font-weight:700;text-decoration:none;border-radius:9px;font-size:15px">Confirmar / Confirm</a>
    <p style="margin:24px 0 0;font-size:12px;line-height:1.6;color:#7e8a99">Si no fuiste t\u00FA, ignora este correo. / If this wasn't you, just ignore this email.</p>
    <p style="margin:14px 0 0;font-size:12px;color:#5f6b7a;word-break:break-all">${confirmUrl}</p>
  </div></body></html>`;
}

export default async (req) => {
  if (req.method !== "POST") return json({ error: "method" }, 405);

  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM } = process.env;
  const SITE_URL = (process.env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !RESEND_API_KEY || !EMAIL_FROM) {
    console.error("subscribe: missing env");
    return json({ error: "server" }, 500);
  }

  let body;
  try { body = await req.json(); } catch { return json({ error: "bad_json" }, 400); }

  const email = String(body?.email || "").trim().toLowerCase();
  const mode = String(body?.mode || "");
  const settings = (body && typeof body.settings === "object" && body.settings) || {};
  if (!EMAIL_RE.test(email) || email.length > 254) return json({ error: "email" }, 400);
  if (!MODES.has(mode)) return json({ error: "mode" }, 400);

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  });

  // look up existing row (by lowercased email)
  const { data: existing, error: selErr } = await supabase
    .from("subscriptions")
    .select("id, active, token")
    .eq("email", email)
    .maybeSingle();
  if (selErr) { console.error("subscribe: select", selErr); return json({ error: "db" }, 502); }

  let token, needConfirm;
  if (existing) {
    token = existing.token;
    needConfirm = !existing.active;       // confirmed users skip re-confirmation
    const patch = { mode, settings };
    if (!existing.active) patch.last_sent = null; // reset cadence for pending
    const { error } = await supabase.from("subscriptions").update(patch).eq("id", existing.id);
    if (error) { console.error("subscribe: update", error); return json({ error: "db" }, 502); }
  } else {
    token = (globalThis.crypto?.randomUUID && globalThis.crypto.randomUUID()) || undefined;
    const row = { email, mode, settings, active: false, last_sent: null, last_price: null };
    if (token) row.token = token;
    const { data, error } = await supabase
      .from("subscriptions").insert(row).select("token").single();
    if (error) { console.error("subscribe: insert", error); return json({ error: "db" }, 502); }
    token = data.token;
    needConfirm = true;
  }

  if (!needConfirm) return json({ ok: true, updated: true });

  // send (or re-send) the confirmation email
  const confirmUrl = `${SITE_URL}/.netlify/functions/confirm?token=${encodeURIComponent(token)}`;
  try {
    const sent = await fetch(`${RESEND_API}/emails`, {
      method: "POST",
      headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from: EMAIL_FROM,
        to: email,
        subject: "Confirma tu suscripci\u00F3n / Confirm your Bitcoin Risk alerts",
        html: confirmHtml(SITE_URL, confirmUrl),
      }),
    });
    if (!sent.ok) { console.error("subscribe: send", sent.status, await sent.text()); return json({ error: "send" }, 502); }
  } catch (e) { console.error("subscribe: send threw", e); return json({ error: "network" }, 502); }

  return json({ ok: true, pending: true });
};
