// netlify/functions/confirm.mjs  —  GET /.netlify/functions/confirm?token=...
// Activates a pending subscription (active:true). Idempotent.
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SITE_URL

import { createClient } from "@supabase/supabase-js";

function page(siteUrl, kind) {
  const ok = kind === "ok" || kind === "already";
  const title = !ok ? "Enlace no v\u00E1lido / Invalid link"
    : kind === "already" ? "Ya estabas suscrito / Already subscribed"
    : "\u00A1Listo! Suscripci\u00F3n confirmada / You're subscribed!";
  const msg = !ok
    ? `<p style="font-size:15px;line-height:1.6;color:#cfd6e0">Este enlace no es v\u00E1lido. Intenta suscribirte de nuevo.<br>This link is invalid. Please try subscribing again.</p>`
    : `<p style="font-size:15px;line-height:1.6;color:#cfd6e0">Recibir\u00E1s tus avisos seg\u00FAn la opci\u00F3n que elegiste.<br>You'll receive alerts based on the option you chose.</p>`;
  return `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bitcoin Risk</title></head>
  <body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:520px;margin:0 auto;padding:48px 22px;text-align:center">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk</div>
    <h1 style="margin:14px 0 14px;font-size:24px;color:${ok ? "#3fb950" : "#f0883e"}">${title}</h1>
    ${msg}
    <a href="${siteUrl}" style="display:inline-block;margin-top:14px;padding:13px 26px;background:#f7931a;color:#0a0d13;font-weight:700;text-decoration:none;border-radius:9px;font-size:15px">Ir al panel / Open dashboard</a>
  </div></body></html>`;
}

export default async (req) => {
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY } = process.env;
  const SITE_URL = (process.env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  const html = (kind) => new Response(page(SITE_URL, kind), {
    status: kind === "bad" ? 400 : 200, headers: { "Content-Type": "text/html; charset=utf-8" },
  });

  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) { console.error("confirm: missing env"); return html("bad"); }

  const token = String(new URL(req.url).searchParams.get("token") || "");
  if (!token) return html("bad");

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });

  const { data: row, error: selErr } = await supabase
    .from("subscriptions").select("id, active").eq("token", token).maybeSingle();
  if (selErr) { console.error("confirm: select", selErr); return html("bad"); }
  if (!row) return html("bad");
  if (row.active) return html("already");

  const { error } = await supabase
    .from("subscriptions")
    .update({ active: true, confirmed_at: new Date().toISOString() })
    .eq("id", row.id);
  if (error) { console.error("confirm: update", error); return html("bad"); }

  return html("ok");
};
