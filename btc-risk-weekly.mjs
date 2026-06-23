// btc-risk-weekly.mjs
// ============================================================================
// Weekly BTC risk -> BUY / HOLD / SELL email, with ZERO Python and no PC needed.
// Runs forever in the cloud on EITHER:
//    (A) Netlify Scheduled Functions  (you already use Netlify + Resend), or
//    (B) GitHub Actions cron          (no website required).
//
// PUBLIC API (for the new multi-user notification system):
//    import { getLatestRisk } from "./btc-risk-weekly.mjs";
//    const reading = await getLatestRisk();
//    // returns { date, risk, price, conf, live, ... } — same shape as before
//
// It reproduces the SAME risk metric as the dashboard (btc_risk_model.py),
// straight from the Coin Metrics dataset, AND extends it with the live BTC price
// from CoinGecko (recomputing the price-driven parts, holding the on-chain parts) —
// exactly like the dashboard's "Refresh".
//
// ----------------------------------------------------------------------------
// REQUIRED ENVIRONMENT VARIABLES (set in Netlify UI or GitHub repo secrets):
//   RESEND_API_KEY   your Resend key (re_...)
//   EMAIL_FROM       a Resend-verified sender, e.g. alerts@adelaidaalejandria.com
//   EMAIL_TO         where to send, e.g. you@example.com
// OPTIONAL:
//   BTC_DCA_BASE     your weekly base $X for buy amounts (default 100)
//   BTC_STACK        your current BTC holdings, to show sell quantities (e.g. 0.85)
//   BTC_CSV_FILE     local file path instead of fetching (used only for testing)
//   BTC_SPOT_OVERRIDE  force a "today" price instead of calling CoinGecko (testing)
// ----------------------------------------------------------------------------
// NOT FINANCIAL ADVICE. This automates a rule; it does not predict the future.
// ============================================================================

import fs from "node:fs/promises";

const CSV_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv";

// ---- model constants (identical to btc_risk_model.py) ----------------------
const PARAMS  = { mayer:[1.60,0.55], logdev:[0.00,0.90], roc:[0.55,0.42],
                  mvrv:[2.25,0.90], puell:[1.70,0.95] };
const WEIGHTS = { mayer:0.12, logdev:0.13, ath:0.15, roc:0.10, mvrv:0.30, puell:0.20 };
const ATH_EXP = 1.5, EMA_ALPHA = 0.5;          // span=3 -> alpha = 2/(3+1)
const SMA_MAYER = 200, WIN_LOGDEV = 365, ROC_LAG = 90, SMA_PUELL = 365;
const MINP = { mayer:50, logdev:60, puell:120 };

const sig = (x,c,b) => { if(!Number.isFinite(x)) return NaN;
  const v = 1/(1+Math.exp(-(x-c)/b)); return v<0?0:v>1?1:v; };

// trailing-window mean over [i-win+1 .. i], skipping NaN, needs >= minp values
function rollMean(a, win, minp){
  const n=a.length, out=new Array(n).fill(NaN);
  for(let i=0;i<n;i++){
    let s=0,c=0; const j0=Math.max(0,i-win+1);
    for(let j=j0;j<=i;j++){ const v=a[j]; if(Number.isFinite(v)){ s+=v; c++; } }
    if(c>=minp) out[i]=s/c;
  }
  return out;
}
// trailing-window sample std (ddof=1), skipping NaN
function rollStd(a, win, minp){
  const n=a.length, out=new Array(n).fill(NaN);
  for(let i=0;i<n;i++){
    let s=0,c=0; const j0=Math.max(0,i-win+1);
    for(let j=j0;j<=i;j++){ const v=a[j]; if(Number.isFinite(v)){ s+=v; c++; } }
    if(c>=minp && c>=2){
      const m=s/c; let q=0;
      for(let j=j0;j<=i;j++){ const v=a[j]; if(Number.isFinite(v)) q+=(v-m)*(v-m); }
      out[i]=Math.sqrt(q/(c-1));
    }
  }
  return out;
}

// ---- parse CSV: keep time, PriceUSD, CapMVRVCur, IssTotUSD ------------------
function parseCSV(text){
  const lines = text.split(/\r?\n/).filter(l=>l.length);
  const head = lines[0].split(",");
  const iT = head.indexOf("time"), iP = head.indexOf("PriceUSD"),
        iM = head.indexOf("CapMVRVCur"), iI = head.indexOf("IssTotUSD");
  if(iT<0||iP<0||iM<0||iI<0) throw new Error("CSV missing required columns");
  const rows=[];
  for(let k=1;k<lines.length;k++){
    const f=lines[k].split(",");
    const p=parseFloat(f[iP]);
    if(!(p>0)) continue;                              // drop missing/zero price
    rows.push({ time:f[iT].slice(0,10),
                price:p,
                mvrv:parseFloat(f[iM]),
                iss:parseFloat(f[iI]) });
  }
  rows.sort((a,b)=> a.time<b.time?-1:a.time>b.time?1:0);
  return rows;
}

// ---- compute the full risk series, return the latest fully-populated row ---
function computeLatest(rows){
  const n=rows.length;
  const P=rows.map(r=>r.price), lnP=P.map(Math.log), ISS=rows.map(r=>r.iss);

  const smaMayer = rollMean(P, SMA_MAYER, MINP.mayer);
  const meanLn   = rollMean(lnP, WIN_LOGDEV, MINP.logdev);
  const stdLn    = rollStd(lnP, WIN_LOGDEV, MINP.logdev);
  const smaIss   = rollMean(ISS, SMA_PUELL, MINP.puell);

  // cummax of price
  const cmax=new Array(n); let mx=-Infinity;
  for(let i=0;i<n;i++){ if(P[i]>mx) mx=P[i]; cmax[i]=mx; }

  const keys=["mayer","logdev","ath","roc","mvrv","puell"];
  const norm=keys.reduce((o,k)=>(o[k]=new Array(n).fill(NaN),o),{});
  const raw=new Array(n).fill(NaN);

  for(let i=0;i<n;i++){
    const mayer  = Number.isFinite(smaMayer[i]) ? P[i]/smaMayer[i] : NaN;
    const logdev = (Number.isFinite(meanLn[i])&&Number.isFinite(stdLn[i])&&stdLn[i]>0)
                   ? (lnP[i]-meanLn[i])/stdLn[i] : NaN;
    const athRaw = P[i]/cmax[i];
    const roc    = i>=ROC_LAG ? P[i]/P[i-ROC_LAG]-1 : NaN;
    const mvrv   = rows[i].mvrv;
    const puell  = Number.isFinite(smaIss[i]) && smaIss[i]>0 ? ISS[i]/smaIss[i] : NaN;

    norm.mayer[i]  = sig(mayer,  ...PARAMS.mayer);
    norm.logdev[i] = sig(logdev, ...PARAMS.logdev);
    norm.ath[i]    = Number.isFinite(athRaw) ? Math.pow(Math.min(1,Math.max(0,athRaw)), ATH_EXP) : NaN;
    norm.roc[i]    = sig(roc,    ...PARAMS.roc);
    norm.mvrv[i]   = sig(mvrv,   ...PARAMS.mvrv);
    norm.puell[i]  = sig(puell,  ...PARAMS.puell);

    let wsum=0, acc=0;
    for(const k of keys){ const v=norm[k][i];
      if(Number.isFinite(v)){ acc+=WEIGHTS[k]*v; wsum+=WEIGHTS[k]; } }
    raw[i] = wsum>0 ? acc/wsum : NaN;
  }

  const risk=new Array(n).fill(NaN); let ema=NaN;
  for(let i=0;i<n;i++){
    const r=raw[i]; if(!Number.isFinite(r)) continue;
    ema = Number.isFinite(ema) ? EMA_ALPHA*r + (1-EMA_ALPHA)*ema : r;
    risk[i] = ema<0?0:ema>1?1:ema;
  }

  let idx=-1;
  for(let i=n-1;i>=0;i--){
    const all = keys.every(k=>Number.isFinite(norm[k][i]));
    if(all && Number.isFinite(risk[i])){ idx=i; break; }
  }
  if(idx<0) throw new Error("no fully-populated row found");

  const conf = confFrom(keys.map(k=>norm[k][idx]), risk[idx]);

  let cmaxIdx=-Infinity; for(let i=0;i<=idx;i++) if(P[i]>cmaxIdx) cmaxIdx=P[i];

  return {
    date: rows[idx].time, risk: risk[idx], price: P[idx], conf,
    prices: P.slice(0, idx+1),
    lastRisk: risk[idx],
    heldMvrv: norm.mvrv[idx],
    heldPuell: norm.puell[idx],
    cummax: cmaxIdx,
  };
}

function confFrom(vals, risk){
  const v = vals.filter(Number.isFinite);
  const m = v.reduce((a,b)=>a+b,0)/v.length;
  const sd = v.length>1 ? Math.sqrt(v.reduce((a,b)=>a+(b-m)*(b-m),0)/(v.length-1)) : 0;
  const agree = Math.min(1,Math.max(0,1-2*sd));
  const extreme = Math.min(1,Math.max(0,2*Math.abs(risk-0.5)));
  return Math.round(Math.min(10,Math.max(0,10*(0.78*agree+0.22*extreme))));
}

function liveStep(P, i, prevRisk, runMax, heldMvrv, heldPuell){
  const px = P[i];
  const m0 = Math.max(0, i-(SMA_MAYER-1));
  let sM=0,cM=0; for(let j=m0;j<=i;j++){ sM+=P[j]; cM++; }
  const s1 = sig(px/(sM/cM), ...PARAMS.mayer);

  const l0 = Math.max(0, i-(WIN_LOGDEV-1));
  let sL=0,cL=0; for(let j=l0;j<=i;j++){ sL+=Math.log(P[j]); cL++; }
  const lm=sL/cL; let q=0; for(let j=l0;j<=i;j++){ const d=Math.log(P[j])-lm; q+=d*d; }
  const ls = cL>1 ? Math.sqrt(q/(cL-1)) : 0;
  const s2 = sig(ls>0 ? (Math.log(px)-lm)/ls : 0, ...PARAMS.logdev);

  const s3 = Math.pow(Math.min(px/runMax,1), ATH_EXP);
  const s4 = i-ROC_LAG>=0 ? sig(px/P[i-ROC_LAG]-1, ...PARAMS.roc) : NaN;

  const subs = { mayer:s1, logdev:s2, ath:s3, roc:s4, mvrv:heldMvrv, puell:heldPuell };
  let num=0, den=0;
  for(const k of Object.keys(WEIGHTS)){ const v=subs[k];
    if(Number.isFinite(v)){ num+=WEIGHTS[k]*v; den+=WEIGHTS[k]; } }
  const raw = den>0 ? num/den : prevRisk;
  let risk = EMA_ALPHA*raw + (1-EMA_ALPHA)*prevRisk;
  risk = risk<0?0:risk>1?1:risk;
  const conf = confFrom(Object.values(subs), risk);
  return { risk, conf };
}

const todayISO = () => new Date().toISOString().slice(0,10);

async function fetchSpotAny(cgHeaders){
  const sources = [
    ["Coinbase", async()=>{ const r=await fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot");
        if(!r.ok) throw new Error("HTTP "+r.status); const j=await r.json(); return parseFloat(j.data.amount); }],
    ["Kraken", async()=>{ const r=await fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD");
        if(!r.ok) throw new Error("HTTP "+r.status); const j=await r.json();
        const k=Object.keys(j.result)[0]; return parseFloat(j.result[k].c[0]); }],
    ["CoinGecko", async()=>{ const r=await fetch("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",{headers:cgHeaders});
        if(!r.ok) throw new Error("HTTP "+r.status); const j=await r.json(); return j.bitcoin.usd; }],
  ];
  for(const [name, fn] of sources){
    try { const v = await fn(); if(v>0){ console.log("live spot via "+name+": $"+Math.round(v)); return v; } }
    catch(e){ console.log(name+" spot failed: "+e.message); }
  }
  return null;
}

async function fetchFreshDaily(lastDate){
  const override = process.env.BTC_SPOT_OVERRIDE;
  if(override) return [{ date:todayISO(), price:parseFloat(override) }];

  const cgKey = process.env.COINGECKO_API_KEY;
  const cgHeaders = cgKey ? { "x-cg-demo-api-key": cgKey } : {};

  try {
    const r = await fetch("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365",
                          {cache:"no-store", headers:cgHeaders});
    if(r.ok){
      const j = await r.json();
      if(j.prices && j.prices.length){
        const byDay = {};
        for(const [ms,price] of j.prices){ byDay[new Date(ms).toISOString().slice(0,10)] = price; }
        const fresh = Object.keys(byDay).sort().map(d=>({date:d, price:byDay[d]})).filter(p=>p.date > lastDate);
        if(fresh.length){ console.log("live daily series via CoinGecko ("+fresh.length+" day(s))"); return fresh; }
      }
    } else { console.log("CoinGecko market_chart HTTP "+r.status+" -> trying spot sources"); }
  } catch(e){ console.log("CoinGecko market_chart failed ("+e.message+") -> trying spot sources"); }

  const spot = await fetchSpotAny(cgHeaders);
  if(spot != null){
    const d = todayISO();
    return d > lastDate ? [{ date:d, price:spot }] : [];
  }
  throw new Error("no live price source reachable");
}

// ---- map risk -> simple BUY / HOLD / SELL instruction ----------------------
function actionFor(risk, baseX, stack){
  let kind, mult=0, parts=0, color, regime;
  if(risk<0.10){ kind="BUY";  mult=5; color="#2ea043"; regime="ACCUMULATE — MAX"; }
  else if(risk<0.20){ kind="BUY";  mult=4; color="#2ea043"; regime="ACCUMULATE — HIGH"; }
  else if(risk<0.30){ kind="BUY";  mult=3; color="#3fb950"; regime="ACCUMULATE"; }
  else if(risk<0.60){ kind="HOLD"; color="#d29922"; regime="HOLD"; }
  else if(risk<0.70){ kind="SELL"; parts=2; color="#d2691e"; regime="DISTRIBUTE"; }
  else if(risk<0.80){ kind="SELL"; parts=3; color="#e8590c"; regime="DISTRIBUTE"; }
  else if(risk<0.90){ kind="SELL"; parts=4; color="#f03e3e"; regime="DISTRIBUTE — HEAVY"; }
  else { kind="SELL"; parts=5; color="#ff4d4d"; regime="DISTRIBUTE — MAX"; }

  let line;
  if(kind==="BUY"){
    line = `Invest $${Math.round(mult*baseX).toLocaleString()} this week (${mult}× your $${baseX} base).`;
  } else if(kind==="SELL"){
    let q="";
    if(stack>0){ const b=(parts/15)*stack; q=` ≈ ${b.toFixed(4)} BTC`; }
    line = `Sell ${parts}/15 of your stack${q}.`;
  } else {
    line = `Do nothing this week — wait for risk to fall below 0.30 or rise above 0.60.`;
  }
  return { kind, mult, parts, color, regime, line };
}

// ---- build + send the email via Resend -------------------------------------
function buildEmail(reading, act){
  const r = reading.risk;
  const subject = `BTC weekly: ${act.kind}` +
    (act.kind==="BUY" ? ` ${act.mult}×` : act.kind==="SELL" ? ` ${act.parts}/15` : "") +
    ` — risk ${r.toFixed(3)}`;

  const bands = [
    ["&lt; 0.10","BUY 5× X","#2ea043"],["0.10–0.20","BUY 4× X","#2ea043"],
    ["0.20–0.30","BUY 3× X","#3fb950"],["0.30–0.60","HOLD","#d29922"],
    ["0.60–0.70","SELL 2/15","#d2691e"],["0.70–0.80","SELL 3/15","#e8590c"],
    ["0.80–0.90","SELL 4/15","#f03e3e"],["0.90–1.00","SELL 5/15","#ff4d4d"],
  ];
  const inBand = (lbl)=>{ const t={"&lt; 0.10":[0,.10],"0.10–0.20":[.10,.20],"0.20–0.30":[.20,.30],
    "0.30–0.60":[.30,.60],"0.60–0.70":[.60,.70],"0.70–0.80":[.70,.80],"0.80–0.90":[.80,.90],
    "0.90–1.00":[.90,1.01]}[lbl]; return r>=t[0]&&r<t[1]; };
  const rows = bands.map(([rng,a,c])=>
    `<tr style="${inBand(rng)?"background:#1c1c26;":""}">
       <td style="padding:5px 10px;border:1px solid #2a2a33">${rng}</td>
       <td style="padding:5px 10px;border:1px solid #2a2a33;color:${c};font-weight:600">${a}</td></tr>`).join("");

  const html = `
  <div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0a0a0f;color:#c9d1d9;padding:24px;max-width:560px;margin:auto;border-radius:14px">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#7d8590">BTC weekly check · ${reading.date}${reading.live?" · live price":""}</div>
    <div style="font-size:34px;font-weight:800;color:${act.color};line-height:1.05;margin:10px 0 2px">${act.kind}</div>
    <div style="font-size:16px;margin-bottom:12px">${act.line}</div>
    <div style="background:#101019;border:1px solid #262630;border-left:4px solid ${act.color};border-radius:10px;padding:12px 14px;margin-bottom:16px">
      <div style="font-size:13px;color:#7d8590">Risk score</div>
      <div style="font-size:30px;font-weight:750;color:${act.color}">${r.toFixed(3)}<span style="font-size:13px;color:#7d8590;font-weight:400"> &nbsp;${act.regime}</span></div>
      <div style="font-size:12.5px;color:#7d8590;margin-top:4px">Confidence ${reading.conf}/10 · BTC $${Math.round(reading.price).toLocaleString()}</div>
    </div>
    <table style="border-collapse:collapse;width:100%;font-size:13px;color:#bcc4cf">
      <tr><th style="text-align:left;padding:5px 10px;border:1px solid #2a2a33;background:#15151f">Risk band</th>
          <th style="text-align:left;padding:5px 10px;border:1px solid #2a2a33;background:#15151f">Action</th></tr>
      ${rows}
    </table>
    <div style="font-size:11.5px;color:#5a6066;margin-top:16px;line-height:1.5">
      Transparent approximation of the Into-the-Cryptoverse risk band — not the official metric, and
      <b>not financial advice</b>. The model embeds hindsight calibration; live results will be more muted than backtests.
    </div>
  </div>`;

  const text = `BTC weekly check ${reading.date}\n${act.kind} — ${act.line.replace(/<[^>]+>/g,"")}\n`+
    `Risk ${r.toFixed(3)} (${act.regime}), confidence ${reading.conf}/10, BTC $${Math.round(reading.price).toLocaleString()}\n(not financial advice)`;
  return { subject, html, text };
}

async function sendEmail({subject, html, text}){
  const key = process.env.RESEND_API_KEY, from = process.env.EMAIL_FROM, to = process.env.EMAIL_TO;
  if(!key || !from || !to) throw new Error("Set RESEND_API_KEY, EMAIL_FROM and EMAIL_TO");
  const resp = await fetch("https://api.resend.com/emails", {
    method:"POST",
    headers:{ "Authorization":`Bearer ${key}`, "Content-Type":"application/json" },
    body: JSON.stringify({ from, to:[to], subject, html, text })
  });
  if(!resp.ok){ throw new Error(`Resend ${resp.status}: ${await resp.text()}`); }
  return await resp.json();
}

// ============================================================================
// NEW PUBLIC API — getLatestRisk()
// ============================================================================
export async function getLatestRisk(){
  let text;
  const local = process.env.BTC_CSV_FILE;
  if(local){ text = await fs.readFile(local, "utf8"); }
  else {
    const r = await fetch(CSV_URL);
    if(!r.ok) throw new Error(`CSV fetch failed: ${r.status}`);
    text = await r.text();
  }

  const state = computeLatest(parseCSV(text));
  let reading = { date:state.date, risk:state.risk, price:state.price, conf:state.conf, live:false };

  try {
    const fresh = await fetchFreshDaily(state.date);
    if(fresh.length){
      const P = state.prices.slice();
      let prevRisk = state.lastRisk, runMax = state.cummax, last = null;
      for(const pt of fresh){
        P.push(pt.price);
        const i = P.length-1;
        runMax = Math.max(runMax, pt.price);
        const { risk, conf } = liveStep(P, i, prevRisk, runMax, state.heldMvrv, state.heldPuell);
        prevRisk = risk;
        last = { date:pt.date, risk, price:pt.price, conf, live:true };
      }
      reading = last;
      console.log(`live-extended with ${fresh.length} day(s) from CoinGecko`);
    } else {
      console.log("no live day newer than dataset; using dataset value");
    }
  } catch(e){
    console.log("live price unavailable ("+e.message+"); using dataset value");
  }

  return reading;
}

// ---- the shared routine (now tiny) -----------------------------------------
export async function run(){
  const baseX = parseFloat(process.env.BTC_DCA_BASE || "100");
  const stack = parseFloat(process.env.BTC_STACK || "0");

  const reading = await getLatestRisk();

  const act = actionFor(reading.risk, baseX, stack);
  console.log(`[${reading.date}${reading.live?" live":" dataset"}] risk=${reading.risk.toFixed(4)} conf=${reading.conf} -> ${act.kind} ${act.mult||act.parts||""}`);

  if(process.env.DRY_RUN === "1"){ console.log("DRY_RUN: not sending."); return reading; }
  const email = buildEmail(reading, act);
  const res = await sendEmail(email);
  console.log("sent:", res?.id || "ok");
  return reading;
}

// ---- (A) Netlify Scheduled Function ----------------------------------------
export const config = { schedule: "0 7 * * 1" };
export default async () => {
  try { await run(); return new Response("sent"); }
  catch(e){ console.error(e); return new Response("error: "+e.message, {status:500}); }
};

// ---- (B) GitHub Actions / CLI ---------------------------------------------
if (process.argv[1] && process.argv[1].endsWith("btc-risk-weekly.mjs")) {
  run().then(()=>process.exit(0)).catch(e=>{ console.error(e); process.exit(1); });
}