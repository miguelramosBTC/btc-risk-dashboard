// lib/supabase.js
// Tiny zero-dependency Supabase REST (PostgREST) client. It only uses fetch, so the exact
// same code runs on the Cloudflare Workers runtime (Pages Functions) AND in Node (the
// GitHub Action) — no @supabase/supabase-js, no nodejs_compat flag, tiny cold starts.
//
// Always call this server-side with the SERVICE ROLE key. Never expose that key to the
// browser; the dashboard only ever talks to these functions, never to Supabase directly.
//
// `env` is whatever object holds the vars: context.env in a Pages Function, process.env in Node.

export function sb(env) {
  const base = String(env.SUPABASE_URL).replace(/\/+$/, "") + "/rest/v1";
  const key = env.SUPABASE_SERVICE_ROLE_KEY;
  const H = { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" };

  async function call(method, path, { body, prefer } = {}) {
    const headers = prefer ? { ...H, Prefer: prefer } : H;
    const r = await fetch(`${base}/${path}`, {
      method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`supabase ${method} ${path.split("?")[0]}: ${r.status} ${await r.text()}`);
    const txt = await r.text();               // return=minimal / 204 -> empty
    return txt ? JSON.parse(txt) : null;
  }

  return {
    // query is a raw PostgREST query string, e.g. "email=eq.a%40b.com&select=id,active&limit=1"
    select: (table, query) => call("GET", `${table}?${query}`),
    insert: (table, row, ret = "representation") =>
      call("POST", table, { body: row, prefer: `return=${ret}` }),
    update: (table, filter, patch, ret = "representation") =>
      call("PATCH", `${table}?${filter}`, { body: patch, prefer: `return=${ret}` }),
  };
}
