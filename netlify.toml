// netlify/functions/suggest.mjs  —  POST /.netlify/functions/suggest
// Body: { email, message }. Stores the suggestion in the Suggestions table.
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

import { createClient } from "@supabase/supabase-js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

export default async (req) => {
  if (req.method !== "POST") return json({ error: "method" }, 405);

  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY } = process.env;
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) { console.error("suggest: missing env"); return json({ error: "server" }, 500); }

  let body;
  try { body = await req.json(); } catch { return json({ error: "bad_json" }, 400); }

  const message = String(body?.message || "").trim();
  if (message.length < 12 || message.length > 5000) return json({ error: "message" }, 400);
  const rawEmail = String(body?.email || "").trim().toLowerCase();
  const email = EMAIL_RE.test(rawEmail) && rawEmail.length <= 254 ? rawEmail : null;

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { error } = await supabase.from("Suggestions").insert({ email, message });
  if (error) { console.error("suggest:", error); return json({ error: "db" }, 502); }

  return json({ ok: true });
};
