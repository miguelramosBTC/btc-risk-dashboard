// functions/api/billing/checkout.js  ->  POST /api/billing/checkout
//
// Creates a Stripe Checkout session and returns { url } for the browser to
// redirect to. One endpoint, two modes (the frontend buttons pick the type):
//
//   { "type": "donation",     "amount_cents": 500, "method": "card" }   one-time, EUR cents
//        method (optional): "card" | "applepay" | "googlepay" | "paypal".
//        Wallets ride on the card payment method; the hosted page shows the
//        Apple/Google Pay button automatically where the device supports it.
//        "paypal" requires PayPal to be enabled in Stripe -> Payment methods.
//   { "type": "subscription", "plan": "monthly"|"yearly", "email": "opt@x.com" }
//
// Apple Pay: nothing special needed here. Checkout is Stripe-hosted
// (checkout.stripe.com), where Apple Pay appears automatically on Safari /
// supported devices, alongside cards. No domain registration required for
// the hosted page.
//
// Env: STRIPE_SECRET_KEY,
//      STRIPE_PRICE_PRO_MONTHLY ($0.99/mo price id; STRIPE_PRICE_PRO_ID is
//      still read as a fallback so existing setups keep working),
//      STRIPE_PRICE_PRO_YEARLY (EUR 9.99/yr price id),
//      SITE_URL (falls back to the request origin). SUPABASE_* reused for a
//      light anti-abuse cap.

import { sb } from "../../../lib/supabase.js";
import { stripeCall } from "../../../lib/stripe.js";

const MIN_CENTS = 100;      // 1 EUR
const MAX_CENTS = 50000;    // 500 EUR
const DEFAULT_CENTS = 500;  // 5 EUR
const SESSIONS_PER_IP_DAY = 20;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });

export function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

/* Light per-IP cap on session creation (anti-abuse, not billing). Reuses the
   Phase 1 api_usage table with a "checkout:" prefix so no new table is needed.
   Fails OPEN — never blocks a paying supporter on a DB hiccup. */
async function checkoutCap(env, ip) {
  try {
    if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) return true;
    const db = sb(env);
    const day = new Date().toISOString().slice(0, 10);
    const key = "checkout:" + ip;
    const q = `day=eq.${day}&ip=eq.${encodeURIComponent(key)}`;
    const rows = await db.select("api_usage", `${q}&select=count&limit=1`);
    if (!rows || !rows.length) {
      try { await db.insert("api_usage", { day, ip: key, count: 1 }, "minimal"); } catch (e) {}
      return true;
    }
    if (rows[0].count + 1 > SESSIONS_PER_IP_DAY) return false;
    await db.update("api_usage", q, { count: rows[0].count + 1 }, "minimal");
    return true;
  } catch (e) {
    console.error("checkout cap (fail-open):", e.message);
    return true;
  }
}

export async function onRequestPost(context) {
  const { request, env } = context;
  if (!env.STRIPE_SECRET_KEY) return json({ error: "billing_not_configured" }, 500);

  let body;
  try { body = await request.json(); } catch (e) { return json({ error: "bad_json" }, 400); }

  const type = String(body.type || "").toLowerCase();
  if (type !== "donation" && type !== "subscription") {
    return json({ error: "bad_type", detail: "type must be donation or subscription" }, 400);
  }

  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (!(await checkoutCap(env, ip))) {
    return json({ error: "rate_limited", detail: "Too many checkout attempts today." }, 429);
  }

  const site = String(env.SITE_URL || new URL(request.url).origin).replace(/\/+$/, "");

  try {
    let session;
    if (type === "donation") {
      let cents = body.amount_cents === undefined ? DEFAULT_CENTS : Number(body.amount_cents);
      if (!Number.isInteger(cents) || cents < MIN_CENTS || cents > MAX_CENTS) {
        return json({ error: "bad_amount", detail: `amount_cents must be an integer ${MIN_CENTS}-${MAX_CENTS}` }, 400);
      }
      const METHOD_TYPES = { card: ["card"], applepay: ["card"], googlepay: ["card"], paypal: ["paypal"] };
      const method = String(body.method || "").toLowerCase();
      if (method && !(method in METHOD_TYPES)) {
        return json({ error: "bad_method", detail: "method must be card, applepay, googlepay or paypal" }, 400);
      }
      session = await stripeCall(env, "checkout/sessions", {
        mode: "payment",
        submit_type: "donate",
        ...(method ? { payment_method_types: METHOD_TYPES[method] } : {}),
        line_items: [{
          quantity: 1,
          price_data: {
            currency: "eur",
            unit_amount: cents,
            product_data: { name: "Bitcoin Risk — donation" },
          },
        }],
        success_url: `${site}/?thanks=1#contribute`,
        cancel_url: `${site}/#contribute`,
      });
    } else {
      const plan = String(body.plan || "monthly").toLowerCase();
      if (plan !== "monthly" && plan !== "yearly") {
        return json({ error: "bad_plan", detail: "plan must be monthly or yearly" }, 400);
      }
      const priceId = plan === "yearly"
        ? env.STRIPE_PRICE_PRO_YEARLY
        : (env.STRIPE_PRICE_PRO_MONTHLY || env.STRIPE_PRICE_PRO_ID);
      if (!priceId) return json({ error: "billing_not_configured", detail: `no price configured for plan=${plan}` }, 500);
      const email = typeof body.email === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email.trim())
        ? body.email.trim().toLowerCase() : undefined;
      session = await stripeCall(env, "checkout/sessions", {
        mode: "subscription",
        line_items: [{ quantity: 1, price: priceId }],
        ...(email ? { customer_email: email } : {}),
        success_url: `${site}/?pro=success&session_id={CHECKOUT_SESSION_ID}#contribute`,
        cancel_url: `${site}/#contribute`,
      });
    }
    if (!session || !session.url) throw new Error("no session url");
    return json({ url: session.url });
  } catch (e) {
    console.error("checkout:", e.message);
    return json({ error: "stripe_error", detail: "Could not create the checkout session." }, 502);
  }
}
