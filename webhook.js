// functions/api/billing/webhook.js  ->  POST /api/billing/webhook  (called by Stripe)
//
// Connects Stripe to the Phase 2 key system, anchored on the same identity the
// rest of the project uses: a verified email (Stripe verifies it at checkout).
//
//   checkout.session.completed (mode=subscription)
//       -> all ACTIVE keys for that email become tier=pro (+ stripe ids attached)
//       -> if the email has no active key, a fresh PRO key is minted and emailed
//       -> confirmation email either way
//   customer.subscription.deleted
//       -> keys with that stripe_customer_id return to tier=free (still active),
//          and the owner is notified
//   anything else -> acknowledged and ignored
//
// Safety properties:
//   - signature verified on the RAW body (HMAC-SHA256, 5-min tolerance); no
//     valid signature, no processing.
//   - idempotent: processed event ids are recorded in billing_events; retries
//     of an already-processed event are acknowledged without acting twice.
//     (Check happens BEFORE acting; the id is recorded AFTER success, so a
//     failed attempt returns 500 and Stripe's retry gets a clean second run.)
//   - tier changes are themselves idempotent updates.
//
// Env: STRIPE_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
//      RESEND_API_KEY, EMAIL_FROM, SITE_URL.

import { sb } from "../../../lib/supabase.js";
import { verifyStripeSignature } from "../../../lib/stripe.js";
import { sha256Hex, newApiKey, TIERS } from "../../../lib/api.js";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

async function sendMail(env, to, subject, html) {
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.EMAIL_FROM, to: [to], subject, html }),
  });
  if (!r.ok) throw new Error(`resend: ${r.status} ${await r.text()}`);
}

const FOOT = `<p style="color:#888;font-size:12px">bitcoinrisk.net · educational data, not financial advice / datos educativos, no es asesoramiento financiero</p>`;

export async function onRequestPost(context) {
  const { request, env } = context;
  if (!env.STRIPE_WEBHOOK_SECRET) return json({ error: "webhook_not_configured" }, 500);

  const raw = await request.text();
  const ok = await verifyStripeSignature(raw, request.headers.get("Stripe-Signature"), env.STRIPE_WEBHOOK_SECRET);
  if (!ok) return json({ error: "invalid_signature" }, 400);

  let event;
  try { event = JSON.parse(raw); } catch (e) { return json({ error: "bad_json" }, 400); }
  if (!event || !event.id || !event.type) return json({ error: "bad_event" }, 400);

  const db = sb(env);

  // ---- idempotency: skip events we have already fully processed -------------
  try {
    const seen = await db.select("billing_events", `stripe_event_id=eq.${encodeURIComponent(event.id)}&select=id&limit=1`);
    if (seen && seen.length) return json({ received: true, duplicate: true });
  } catch (e) {
    console.error("billing_events check (continuing):", e.message);
  }

  let email = null;
  try {
    // ======================= subscription started ==========================
    if (event.type === "checkout.session.completed") {
      const s = event.data?.object || {};
      if (s.mode !== "subscription") return json({ received: true, ignored: "non-subscription session" });
      email = String(s.customer_details?.email || s.customer_email || "").trim().toLowerCase();
      if (!email) { console.error("completed session without email", event.id); return json({ received: true, ignored: "no email" }); }
      const customer = s.customer || null;
      const subscription = s.subscription || null;

      const keys = await db.select("api_keys",
        `email=eq.${encodeURIComponent(email)}&active=eq.true&select=id,key_prefix,tier`);

      if (keys && keys.length) {
        await db.update("api_keys", `email=eq.${encodeURIComponent(email)}&active=eq.true`, {
          tier: "pro", stripe_customer_id: customer, stripe_subscription_id: subscription,
        }, "minimal");
        await sendMail(env, email, "Welcome to Pro — your API keys are upgraded",
          `<h2>Welcome to Pro 🎉</h2>
           <p>Your ${keys.length === 1 ? "API key is" : `${keys.length} API keys are`} now <b>Pro</b>:
              full history, all components (as published), ${TIERS.pro.per_day.toLocaleString("en-US")} requests/day.</p>
           <p>Upgraded key${keys.length === 1 ? "" : "s"}: <code>${keys.map((k) => k.key_prefix + "…").join(", ")}</code>
              — same keys, no code changes needed.</p>
           <p><i>ES:</i> Tu suscripción Pro está activa. Tus claves existentes ya tienen acceso Pro; no hace falta cambiar nada.</p>
           <p>Manage or revoke keys anytime via the API panel on the site.</p>${FOOT}`);
      } else {
        const { key: rawKey, prefix } = newApiKey();
        await db.insert("api_keys", {
          email, key_hash: await sha256Hex(rawKey), key_prefix: prefix,
          tier: "pro", stripe_customer_id: customer, stripe_subscription_id: subscription,
        }, "minimal");
        await sendMail(env, email, "Welcome to Pro — your new API key",
          `<h2>Welcome to Pro 🎉</h2>
           <p>Here is your <b>Pro API key</b>. Store it safely — it is shown only once and we keep only a hash:</p>
           <p style="font-size:16px"><code>${rawKey}</code></p>
           <p>Use it as <code>Authorization: Bearer &lt;key&gt;</code> against
              <a href="${(env.SITE_URL || "https://bitcoinrisk.net")}/api/risk">/api/risk</a> —
              full history, ${TIERS.pro.per_day.toLocaleString("en-US")} requests/day.</p>
           <p><i>ES:</i> Esta es tu clave API Pro. Guárdala bien: solo se muestra una vez.</p>${FOOT}`);
      }
    }

    // ======================= subscription ended ============================
    else if (event.type === "customer.subscription.deleted") {
      const sub = event.data?.object || {};
      const customer = sub.customer;
      if (!customer) return json({ received: true, ignored: "no customer" });
      const keys = await db.select("api_keys",
        `stripe_customer_id=eq.${encodeURIComponent(customer)}&select=id,email,key_prefix&active=eq.true`);
      if (keys && keys.length) {
        email = keys[0].email;
        await db.update("api_keys", `stripe_customer_id=eq.${encodeURIComponent(customer)}&active=eq.true`, {
          tier: "free", stripe_subscription_id: null,
        }, "minimal");
        try {
          await sendMail(env, email, "Your Pro subscription has ended",
            `<p>Your Pro subscription has ended, so your API key${keys.length === 1 ? "" : "s"} returned to the
             <b>free tier</b> (last 90 days, ${TIERS.free.per_day} requests/day). The keys themselves keep working.</p>
             <p>You can re-subscribe anytime from the Contribute section.</p>
             <p><i>ES:</i> Tu suscripción Pro ha terminado; tus claves vuelven al nivel gratuito y siguen funcionando.</p>${FOOT}`);
        } catch (e) { console.error("downgrade mail (non-fatal):", e.message); }
      }
    }

    // ======================= everything else ===============================
    else {
      return json({ received: true, ignored: event.type });
    }

    // ---- record success for idempotency (best-effort) ----------------------
    try {
      await db.insert("billing_events", { stripe_event_id: event.id, type: event.type, email }, "minimal");
    } catch (e) { console.error("billing_events record (non-fatal):", e.message); }

    return json({ received: true });
  } catch (e) {
    // 500 so Stripe retries; the event id was NOT recorded, so the retry re-runs cleanly.
    console.error("webhook handler:", event.type, e.message);
    return json({ error: "handler_failed" }, 500);
  }
}
