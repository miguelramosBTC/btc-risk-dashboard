// netlify/functions/send-notifications.mjs
// =============================================================================
// THE PER-USER PREFERENCE ENGINE. Runs once a day on Netlify's cron, computes
// today's risk (identical to the dashboard), then for each CONFIRMED subscriber
// decides whether their chosen rule fires today and emails them if so.
//
//   time mode      -> fires on their schedule (daily / weekly:weekday / monthly:dom / yearly)
//   price mode     -> fires when BTC crosses their threshold (edge-triggered, no daily spam)
//   strategy mode  -> sends their DCA call on their cadence, using the dashboard's exact
//                     per-strategy multipliers (Conservative / Moderate / Aggressive)
//
// Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM, SITE_URL
//      COINGECKO_API_KEY (optional, for the live price fill)
// =============================================================================

import { createClient } from "@supabase/supabase-js";
import { getLatestRisk, actionFor, strategyBuy, labelsFor } from "../../btc-risk-weekly.mjs";

const RESEND_API = "https://api.resend.com";
const WEEKDAY = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
const STRAT_NAME = {
  cons: { en: "Conservative", es: "Conservadora" },
  mod:  { en: "Moderate",     es: "Moderada" },
  aggr: { en: "Aggressive",   es: "Agresiva" },
};

export const config = { schedule: "0 8 * * *" }; // every day 08:00 UTC

const ymd = (d) => d.toISOString().slice(0, 10);
const usd = (n) => "$" + Math.round(n).toLocaleString("en-US");

// already sent today? (de-dupe guard)
function sentToday(sub, now) {
  return sub.last_sent && ymd(new Date(sub.last_sent)) === ymd(now);
}

// time / strategy schedules (UTC)
export function scheduleDue(freqOrCadence, settings, now) {
  switch (freqOrCadence) {
    case "hourly":            // daily cron can't do hourly -> treat as daily
    case "daily":   return true;
    case "weekly":  return now.getUTCDay() === (WEEKDAY[settings.weekday] ?? 1);
    case "monthly": return now.getUTCDate() === (parseInt(settings.dom, 10) || 1);
    case "yearly":  return (now.getUTCMonth() + 1) === (parseInt(settings.month, 10) || 1)
                        && now.getUTCDate() === (parseInt(settings.day, 10) || 1);
    default:        return false;
  }
}

// price crossing (edge-triggered). Returns true only when we cross the line.
export function priceCrossed(direction, threshold, price, lastPrice) {
  if (!(threshold > 0) || !(price > 0)) return false;
  const first = lastPrice == null;
  if (direction === "above") return price >= threshold && (first || lastPrice < threshold);
  return price <= threshold && (first || lastPrice > threshold);            // "below"
}

export function actionLine(reading, lang) {
  const a = actionFor(reading.risk), es = lang === "es";
  if (a.kind === "buy")  return es ? `Compra ${a.mult}\u00D7 tu base` : `Buy ${a.mult}\u00D7 your base`;
  if (a.kind === "hold") return es ? "Mantener \u2014 ni comprar ni vender" : "Hold \u2014 neither buy nor sell";
  return es ? `Vende ${a.parts}/15 de tu stack` : `Sell ${a.parts}/15 of your stack`;
}

export function bodyForMode(sub, reading, lang) {
  const es = lang === "es";
  if (sub.mode === "price") {
    const s = sub.settings || {};
    const dirEs = s.direction === "above" ? "por encima de" : "por debajo de";
    const dirEn = s.direction === "above" ? "above" : "below";
    return es
      ? `BTC ${usd(reading.price)} est\u00E1 ${dirEs} tu alerta de ${usd(s.threshold)}.`
      : `BTC ${usd(reading.price)} is ${dirEn} your ${usd(s.threshold)} alert.`;
  }
  if (sub.mode === "strategy") {
    const s = sub.settings || {};
    const rec = strategyBuy(s.strategy, reading.risk, s.base);
    const name = (STRAT_NAME[s.strategy] || STRAT_NAME.mod)[lang];
    if (rec.save) {
      return es
        ? `Estrategia ${name}: esta vez no compres \u2014 guarda efectivo y espera a que el riesgo baje.`
        : `${name} strategy: buy nothing this period \u2014 hold cash and wait for risk to fall.`;
    }
    return es
      ? `Estrategia ${name}: compra ${usd(rec.amount)} (${rec.mult}\u00D7 tu base de ${usd(s.base || 100)}).`
      : `${name} strategy: buy ${usd(rec.amount)} (${rec.mult}\u00D7 your ${usd(s.base || 100)} base).`;
  }
  // time mode
  return actionLine(reading, lang);
}

export function buildEmail(sub, reading, siteUrl) {
  const r = reading.risk, lab = labelsFor(r, "en");
  const rr = r.toFixed(2), pct = (r * 100).toFixed(1);
  const unsub = `${siteUrl}/.netlify/functions/unsubscribe?token=${encodeURIComponent(sub.token)}`;

  let subject;
  if (sub.mode === "price") subject = `BTC ${usd(reading.price)} \u00B7 your alert \u2014 risk ${rr}`;
  else if (sub.mode === "strategy") {
    const rec = strategyBuy(sub.settings?.strategy, r, sub.settings?.base);
    subject = rec.save ? `DCA: hold this period \u2014 risk ${rr}` : `DCA: buy ${usd(rec.amount)} \u2014 risk ${rr}`;
  } else subject = `Bitcoin Risk ${rr} \u00B7 ${labelsFor(r, "en").regime} \u2014 ${reading.date}`;

  const box = (lang) => `
    <div style="margin:6px 0 0;font-size:13px;color:#8b97a7">${labelsFor(r, lang).regime}</div>
    <div style="font-size:16px;font-weight:600;color:#f7931a;margin-top:2px">${bodyForMode(sub, reading, lang)}</div>`;

  const html = `<!doctype html><html><body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:560px;margin:0 auto;padding:28px 22px">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk \u00B7 ${reading.date}${reading.live ? " \u00B7 live" : ""}</div>
    <div style="margin:14px 0 6px;font-size:64px;font-weight:700;color:${lab.color};line-height:1">${rr}</div>
    <div style="font-size:14px;color:#b7c0cd">${pct} / 100 \u00B7 BTC ${usd(reading.price)} \u00B7 conf ${reading.conf}/10</div>
    <div style="margin:22px 0;padding:16px 18px;background:#11161f;border:1px solid #1d2530;border-left:4px solid ${lab.color};border-radius:12px">
      ${box("es")}
      <div style="height:10px"></div>
      ${box("en")}
    </div>
    <a href="${siteUrl}" style="display:inline-block;padding:11px 20px;background:#f7931a;color:#0a0d13;font-weight:600;text-decoration:none;border-radius:9px;font-size:14px">Ver el panel / Open dashboard</a>
    <p style="margin:22px 0 6px;font-size:12px;line-height:1.6;color:#7e8a99">Esto automatiza una regla; no es asesoramiento financiero. / This automates a rule and is not financial advice.</p>
    <p style="margin:0;font-size:12px;color:#5f6b7a">Cancelar suscripci\u00F3n / Unsubscribe: <a href="${unsub}" style="color:#5f6b7a">${unsub}</a></p>
  </div></body></html>`;

  return { subject, html, unsub };
}

async function sendOne(sub, reading, siteUrl) {
  const { RESEND_API_KEY, EMAIL_FROM } = process.env;
  const { subject, html, unsub } = buildEmail(sub, reading, siteUrl);
  const resp = await fetch(`${RESEND_API}/emails`, {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: EMAIL_FROM, to: sub.email, subject, html,
      headers: {
        "List-Unsubscribe": `<${unsub}>`,
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
      },
    }),
  });
  if (!resp.ok) throw new Error(`Resend ${resp.status}: ${await resp.text()}`);
}

export default async () => {
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM } = process.env;
  const SITE_URL = (process.env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !RESEND_API_KEY || !EMAIL_FROM) {
    console.error("send-notifications: missing env");
    return new Response("missing env", { status: 500 });
  }

  const reading = await getLatestRisk();
  console.log(`risk=${reading.risk.toFixed(4)} px=${Math.round(reading.price)} date=${reading.date} live=${reading.live}`);

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { data: subs, error } = await supabase
    .from("subscriptions").select("*").eq("active", true);
  if (error) { console.error("fetch subs:", error); return new Response("db error", { status: 500 }); }

  const now = new Date();
  let sent = 0, evaluated = subs.length;

  for (const sub of subs) {
    try {
      const s = sub.settings || {};
      let due = false;

      if (sub.mode === "time") {
        due = scheduleDue(s.freq, s, now) && !sentToday(sub, now);
      } else if (sub.mode === "strategy") {
        due = scheduleDue(s.cadence, s, now) && !sentToday(sub, now);
      } else if (sub.mode === "price") {
        due = priceCrossed(s.direction, Number(s.threshold), reading.price, sub.last_price) && !sentToday(sub, now);
        // price subs always record the latest price so crossings are detected next run
        await supabase.from("subscriptions").update({ last_price: reading.price }).eq("id", sub.id);
      }

      if (!due) continue;

      await sendOne(sub, reading, SITE_URL);
      await supabase.from("subscriptions")
        .update({ last_sent: now.toISOString() }).eq("id", sub.id);
      sent++;
      await new Promise((r) => setTimeout(r, 120)); // be gentle with the API
    } catch (e) {
      console.error(`send failed for ${sub.email}:`, e.message); // leave last_sent so it retries
    }
  }

  console.log(`evaluated ${evaluated} active subs, sent ${sent}`);
  return new Response(`ok: sent ${sent}/${evaluated}`);
};
