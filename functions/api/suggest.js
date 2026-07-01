// functions/api/suggest.js  ->  POST /api/suggest
// Body: { email, message }. Stores the suggestion in the Suggestions table.
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

import { sb } from "../../lib/supabase.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

export async function onRequestPost(context) {
  const { request, env } = context;
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY } = env;
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) { console.error("suggest: missing env"); return json({ error: "server" }, 500); }

  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }

  const message = String(body?.message || "").trim();
  if (message.length < 12 || message.length > 5000) return json({ error: "message" }, 400);
  const rawEmail = String(body?.email || "").trim().toLowerCase();
  const email = EMAIL_RE.test(rawEmail) && rawEmail.length <= 254 ? rawEmail : null;

  try {
    await sb(env).insert("Suggestions", { email, message }, "minimal");
  } catch (e) { console.error("suggest:", e.message); return json({ error: "db" }, 502); }

  return json({ ok: true });
}
