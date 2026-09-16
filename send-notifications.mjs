// send-notifications.mjs  —  runs daily on GitHub Actions (.github/workflows/btc-notify.yml)
// =============================================================================
// THE PER-USER PREFERENCE ENGINE. Computes today's risk (identical to the dashboard),
// then for each CONFIRMED subscriber decides whether their chosen rule fires today and
// emails them if so.
//
//   time mode      -> fires on their schedule (daily / weekly:weekday / monthly:dom / yearly)
//   price mode     -> fires when BTC crosses their threshold (edge-triggered, no daily spam)
//   strategy mode  -> sends their DCA call on their cadence, using the dashboard's exact
//                     per-strategy multipliers (Conservative / Moderate / Aggressive)
//
// Runs in Node 20 on GitHub's runners — no web host needed. Zero npm dependencies.
// Env (GitHub -> Settings -> Secrets and variables -> Actions):
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM, SITE_URL
//   COINGECKO_API_KEY (optional), DATA_URL (optional; defaults to raw GitHub in the library)
// =============================================================================

import { sb } from "./lib/supabase.js";
import { getLatestRisk, actionFor, strategyBuy, regimeFor, meaningLine } from "./btc-risk-weekly.mjs";

const RESEND_API = "https://api.resend.com";
const WEEKDAY = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
const STRAT_NAME = {
  cons: { en: "Conservative", es: "Conservadora" },
  mod:  { en: "Moderate",     es: "Moderada" },
  aggr: { en: "Aggressive",   es: "Agresiva" },
};

const ymd = (d) => d.toISOString().slice(0, 10);
const usd = (n) => "$" + Math.round(n).toLocaleString("en-US");

function sentToday(sub, now) {
  return sub.last_sent && ymd(new Date(sub.last_sent)) === ymd(now);
}

export function scheduleDue(freqOrCadence, settings, now) {
  switch (freqOrCadence) {
    case "hourly":            // a once-a-day job can't do hourly -> treat as daily
    case "daily":   return true;
    case "weekly":  return now.getUTCDay() === (WEEKDAY[settings.weekday] ?? 1);
    case "monthly": return now.getUTCDate() === (parseInt(settings.dom, 10) || 1);
    case "yearly":  return (now.getUTCMonth() + 1) === (parseInt(settings.month, 10) || 1)
                        && now.getUTCDate() === (parseInt(settings.day, 10) || 1);
    default:        return false;
  }
}

export function priceCrossed(direction, threshold, price, lastPrice) {
  if (!(threshold > 0) || !(price > 0)) return false;
  const first = lastPrice == null;
  if (direction === "above") return price >= threshold && (first || lastPrice < threshold);
  return price <= threshold && (first || lastPrice > threshold);
}

/* Risk-level mode. Describes the reading; it does not instruct. The "about 30%
   of days" clause is the most important sentence in the whole rewrite: without
   it a subscriber who remembers v2 reads 0.82 as a once-a-cycle warning, when on
   the rank scale it is a top-quintile reading that happens three days in ten. */
/* share of days each band covers on the 5,191-row v3 tape */
const V3_FREQ = { bottom: 12, below: 30, above: 28, top: 30 };

export function actionLine(reading, lang) {
  const es = lang === "es";
  if (reading.model === "v2") {
    /* v2's scale, so v2's own words stay correct. */
    const a = actionFor(reading.risk);
    if (a.kind === "buy")  return es ? `Compra ${a.mult}\u00D7 tu base` : `Buy ${a.mult}\u00D7 your base`;
    if (a.kind === "hold") return es ? "Mantener \u2014 ni comprar ni vender" : "Hold \u2014 neither buy nor sell";
    return es ? `Vende ${a.parts}/15 de tu stack` : `Sell ${a.parts}/15 of your stack`;
  }
  const reg = regimeFor(reading.risk, lang, "v3");
  const freq = V3_FREQ[reg.key] ?? null;
  const meaning = meaningLine(reading.risk, reading.date, lang, reading.model);
  const tail = freq == null ? "" : (es
    ? ` Lecturas en este tramo ocurren en torno al ${freq} % de los d\u00EDas, as\u00ED que es una nota de r\u00E9gimen, no una alarma.`
    : ` Readings in this band occur on about ${freq}% of days, so this is a regime note, not an alarm.`);
  return es
    ? `${reg.regime} \u2014 ${meaning}.${tail}`
    : `${reg.regime} \u2014 ${meaning}.${tail}`;
}

/* Shown whenever the v3 tape could not be used. Naming the scale change is the
   point: the number is not comparable with a v3 one. */
export function fallbackLine(reading, lang) {
  if (!reading.fallback) return "";
  return lang === "es"
    ? `Servido por el modelo v2 \u2014 ${reading.fallback}. v2 usa otra escala y sus n\u00FAmeros no son comparables con los de v3.`
    : `Served from the v2 model \u2014 ${reading.fallback}. v2 uses a different scale and its numbers are not comparable with v3's.`;
}

export function bodyForMode(sub, reading, lang) {
  const es = lang === "es";
  if (sub.mode === "price") {
    const s = sub.settings || {};
    const dirEs = s.direction === "above" ? "por encima de" : "por debajo de";
    const dirEn = s.direction === "above" ? "above" : "below";
    /* the live quote the alert actually fired on, not the committed close that
       belongs to the risk number */
    const px = usd(reading.spot ?? reading.price);
    return es
      ? `BTC ${px} est\u00E1 ${dirEs} tu alerta de ${usd(s.threshold)}.`
      : `BTC ${px} is ${dirEn} your ${usd(s.threshold)} alert.`;
  }
  if (sub.mode === "strategy") {
    const s = sub.settings || {};
    const rec = strategyBuy(s.strategy, reading.risk, s.base);
    const name = (STRAT_NAME[s.strategy] || STRAT_NAME.mod)[lang];
    const base = usd(s.base || 100);
    const meaning = meaningLine(reading.risk, reading.date, lang, reading.model);
    if (rec.save) {
      /* Not "wait for risk to fall" \u2014 the tilt is continuous, so this only
         happens at the very top of the recorded range, where it rounds below a
         dollar. Say that, rather than implying a timing instruction. */
      const at = reading.model === "v2"
        ? (es ? `Calculado sobre la ${meaning}.` : `Computed from ${meaning}.`)
        : (es ? `Bitcoin est\u00E1 ${meaning}, el extremo superior del rango registrado.`
              : `Bitcoin is ${meaning} \u2014 the top of the recorded range.`);
      return es
        ? `Estrategia ${name}: la inclinaci\u00F3n queda por debajo de $1 este periodo. ${at}`
        : `${name} strategy: the tilt rounds below $1 this period. ${at}`;
    }
    /* The meaning clause is its own sentence, not spliced into "Bitcoin is ___":
       on the v2 fallback that produced "Bitcoin is the v2 model's blended score",
       and a subject line claiming a percentile for a number that is not one. */
    const why = reading.model === "v2"
      ? (es ? `Calculado sobre la ${meaning} \u2014 una escala distinta de la de v3.`
            : `Computed from ${meaning} \u2014 a different scale from v3's.`)
      : (es ? `Bitcoin est\u00E1 ${meaning}.` : `Bitcoin is ${meaning}.`);
    const tilt = es
      ? `La inclinaci\u00F3n queda ${rec.share < 1 ? "por debajo de" : "en"} tu base de ${base}.`
      : `The tilt sits ${rec.share < 1 ? "below" : "at"} your ${base} base.`;
    return es
      ? `Estrategia ${name}: compra ${usd(rec.amount)} este periodo. ${why} ${tilt} Es una descripci\u00F3n de las condiciones de hoy, no un pron\u00F3stico.`
      : `${name} strategy: buy ${usd(rec.amount)} this period. ${why} ${tilt} This is a description of today's conditions, not a forecast.`;
  }
  return actionLine(reading, lang);
}

export function buildEmail(sub, reading, siteUrl) {
  const r = reading.risk, lab = regimeFor(r, "en", reading.model);
  const rr = r.toFixed(2), pct = (r * 100).toFixed(1);
  const unsub = `${siteUrl}/api/unsubscribe?token=${encodeURIComponent(sub.token)}`;
  const pctOf = Math.round(r * 100);

  let subject;
  if (sub.mode === "price") {
    subject = `BTC ${usd(reading.spot || reading.price)} \u00B7 your alert \u00B7 risk ${rr}`;
  } else if (sub.mode === "strategy") {
    const rec = strategyBuy(sub.settings?.strategy, r, sub.settings?.base);
    /* "of its own history" is the v3 percentile framing and is FALSE of a v2
       blend, so it only travels with a v3 reading. */
    const qual = reading.model === "v3" ? " of its own history" : " (v2 scale)";
    subject = rec.save
      ? `DCA: nothing this period \u00B7 risk ${rr}${qual}`
      : `DCA: buy ${usd(rec.amount)} \u00B7 risk ${rr}${qual}`;
  } else {
    subject = `Bitcoin Risk ${rr} \u00B7 ${lab.regime.toLowerCase()} \u2014 ${reading.date}`;
  }

  const box = (lang) => `
    <div style="margin:6px 0 0;font-size:13px;color:#8b97a7">${regimeFor(r, lang, reading.model).regime}</div>
    <div style="font-size:16px;font-weight:600;color:#f7931a;margin-top:2px">${bodyForMode(sub, reading, lang)}</div>`;

  /* The scale note rides on every email. It is the only defence against a
     subscriber reading a rank with v2's reflexes. */
  const scaleNote = reading.model === "v3"
    ? `<p style="margin:16px 0 0;font-size:12px;line-height:1.6;color:#7e8a99">`
      + `El \u00EDndice es un <b>rango percentil</b>: ${rr} significa m\u00E1s extendido que el ${pctOf}\u00A0% de su propia historia hasta esa ma\u00F1ana. No es una probabilidad. / `
      + `The score is a <b>percentile rank</b>: ${rr} means more extended than ${pctOf}% of its own history up to that morning. It is not a probability.</p>`
    : "";
  const fbNote = reading.fallback
    ? `<div style="margin:16px 0 0;padding:12px 14px;background:#2a1d12;border:1px solid #5a3a1a;border-radius:10px;font-size:12px;line-height:1.6;color:#e0b483">`
      + `${fallbackLine(reading, "es")}<br>${fallbackLine(reading, "en")}</div>`
    : "";
  const bandNote = (reading.model === "v3" && reading.band)
    ? ` \u00B7 banda / band ${reading.band.lo.toFixed(2)}\u2013${reading.band.hi.toFixed(2)}`
    : "";

  const html = `<!doctype html><html><body style="margin:0;background:#0a0d13;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e8edf4">
  <div style="max-width:560px;margin:0 auto;padding:28px 22px">
    <div style="font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#8b97a7">Bitcoin Risk \u00B7 ${reading.date} \u00B7 ${reading.schema_version}</div>
    <div style="margin:14px 0 6px;font-size:64px;font-weight:700;color:${lab.color};line-height:1">${rr}</div>
    <div style="font-size:14px;color:#b7c0cd">${pct} / 100${bandNote} \u00B7 BTC ${usd(reading.price)} \u00B7 conf ${reading.conf}/10</div>
    <div style="margin:22px 0;padding:16px 18px;background:#11161f;border:1px solid #1d2530;border-left:4px solid ${lab.color};border-radius:12px">
      ${box("es")}
      <div style="height:10px"></div>
      ${box("en")}
    </div>
    <a href="${siteUrl}" style="display:inline-block;padding:11px 20px;background:#f7931a;color:#0a0d13;font-weight:600;text-decoration:none;border-radius:9px;font-size:14px">Ver el panel / Open dashboard</a>
    ${fbNote}
    ${scaleNote}
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

export default async function run() {
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY, EMAIL_FROM } = process.env;
  const SITE_URL = (process.env.SITE_URL || "https://bitcoinrisk.net").replace(/\/+$/, "");
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !RESEND_API_KEY || !EMAIL_FROM) {
    console.error("send-notifications: missing env"); process.exitCode = 1; return;
  }

  const reading = await getLatestRisk();
  console.log(`risk=${reading.risk.toFixed(4)} px=${Math.round(reading.price)} date=${reading.date} live=${reading.live}`);

  const db = sb(process.env);
  let subs;
  try { subs = await db.select("subscriptions", "active=eq.true&select=*"); }
  catch (e) { console.error("fetch subs:", e.message); process.exitCode = 1; return; }

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
        due = priceCrossed(s.direction, Number(s.threshold), reading.spot ?? reading.price, sub.last_price) && !sentToday(sub, now);
        await db.update("subscriptions", `id=eq.${sub.id}`, { last_price: reading.price }, "minimal");
      }

      if (!due) continue;

      await sendOne(sub, reading, SITE_URL);
      await db.update("subscriptions", `id=eq.${sub.id}`, { last_sent: now.toISOString() }, "minimal");
      sent++;
      await new Promise((r) => setTimeout(r, 120));
    } catch (e) {
      console.error(`send failed for ${sub.email}:`, e.message); // leave last_sent so it retries
    }
  }

  console.log(`evaluated ${evaluated} active subs, sent ${sent}`);
}

// run when invoked directly (node send-notifications.mjs)
if (process.argv[1] && process.argv[1].endsWith("send-notifications.mjs")) run();
