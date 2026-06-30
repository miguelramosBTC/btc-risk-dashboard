// netlify/functions/unsubscribe.mjs  —  /.netlify/functions/unsubscribe?token=...
// GET  -> deactivate + show a page (clicked link)
// POST -> deactivate + 200 (RFC 8058 one-click, used by the List-Unsubscribe header)
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SITE_URL

import { createClient } from "@supabase/supabase-js";

function page(siteUrl, ok) {
  return `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bitcoin Risk</title></head>
  <body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:520px;margin:0 auto;padding:48px 22px;text-align:center">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk</div>
    <h1 style="margin:14px 0 14px;font-size:24px;color:${ok ? "#e7b53b" : "#f0883e"}">${ok ? "Suscripci\u00F3n cancelada / Unsubscribed" : "Enlace no v\u00E1lido / Invalid link"}</h1>
    <p style="font-size:15px;line-height:1.6;color:#cfd6e0">${ok
      ? "Ya no recibir\u00E1s m\u00E1s correos. Puedes volver cuando quieras.<br>You won't receive any more emails. You can re-subscribe anytime."
      : "Este enlace no es v\u00E1lido.<br>This link is invalid."}</p>
    <a href="${siteUrl}" style="display:inline-block;margin-top:14px;padding:13px 26px;background:#f7931a;color:#0a0d13;font-weight:700;text-decoration:none;border-radius:9px;font-size:15px">Ir al panel / Open dashboard</a>
  </div></body></html>`;
}

async function deactivate(token) {
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY } = process.env;
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !token) return false;
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { data, error } = await supabase
    .from("subscriptions").update({ active: false }).eq("token", token).select("id");
  if (error) { console.error("unsubscribe:", error); return false; }
  return Array.isArray(data) && data.length > 0;
}

export default async (req) => {
  const SITE_URL = (process.env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  const url = new URL(req.url);
  let token = url.searchParams.get("token") || "";

  if (req.method === "POST") {
    if (!token) { try { const b = await req.json(); token = b?.token || ""; } catch {} }
    await deactivate(token);
    return new Response("ok", { status: 200 }); // one-click: 200 regardless
  }

  const ok = await deactivate(token);
  return new Response(page(SITE_URL, ok), {
    status: ok ? 200 : 400, headers: { "Content-Type": "text/html; charset=utf-8" },
  });
};
