// lib/stripe.js
// Tiny zero-dependency Stripe client for Cloudflare Pages Functions, in the same
// spirit as lib/supabase.js: plain fetch, no SDK, no nodejs_compat flag.
//
// - stripeCall(env, path, params): form-encoded POST to the Stripe REST API.
// - verifyStripeSignature(rawBody, header, secret): checks the Stripe-Signature
//   header (HMAC-SHA256 of "t.rawBody", 5-minute tolerance, constant-time compare).
//   Must be given the RAW request body text, byte-for-byte.

const STRIPE_API = "https://api.stripe.com/v1";

/* Flatten {a:{b:1}, c:[{d:2}]} into Stripe's bracket form: a[b]=1, c[0][d]=2 */
export function stripeForm(obj) {
  const out = new URLSearchParams();
  const walk = (val, key) => {
    if (val === undefined || val === null) return;
    if (Array.isArray(val)) val.forEach((v, i) => walk(v, `${key}[${i}]`));
    else if (typeof val === "object") for (const k in val) walk(val[k], key ? `${key}[${k}]` : k);
    else out.append(key, String(val));
  };
  walk(obj, "");
  return out.toString();
}

export async function stripeCall(env, path, params) {
  const r = await fetch(`${STRIPE_API}/${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: stripeForm(params),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = body?.error?.message || `HTTP ${r.status}`;
    throw new Error(`stripe ${path}: ${msg}`);
  }
  return body;
}

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

async function hmacSha256Hex(secret, payload) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload)));
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/* Returns true only for a well-formed, fresh, correctly signed header. */
export async function verifyStripeSignature(rawBody, sigHeader, secret, toleranceSec = 300) {
  if (!sigHeader || !secret) return false;
  let t = null; const v1s = [];
  for (const part of sigHeader.split(",")) {
    const [k, v] = part.split("=", 2).map((s) => s && s.trim());
    if (k === "t") t = v;
    else if (k === "v1" && v) v1s.push(v);
  }
  if (!t || !/^\d+$/.test(t) || v1s.length === 0) return false;
  if (Math.abs(Date.now() / 1000 - Number(t)) > toleranceSec) return false;
  const expected = await hmacSha256Hex(secret, `${t}.${rawBody}`);
  return v1s.some((v) => timingSafeEqual(v, expected));
}
