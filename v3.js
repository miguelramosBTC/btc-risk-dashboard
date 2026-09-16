/* v3.js — Bitcoin Risk v3.0 data layer for the dashboard.
 *
 * Loaded BEFORE app.js. Defines one global, V3.
 *
 * The single rule this file exists to enforce: the official risk number is READ
 * from the committed daily row, never recomputed in the browser. v2 shipped
 * three different "current risk" objects (data.json, the heldSubs gauge, and the
 * matrix); v3 has one. Anything here that moves with spot price is an explicitly
 * labelled overlay and is never called "risk".
 *
 * Fallback is deliberate and loud-in-code, quiet-on-screen: if the window file
 * is missing, malformed or stale, V3.active stays false and app.js keeps serving
 * v2 exactly as before. That lets this file be merged before the v3 tape is
 * bootstrapped, without the page ever showing a hole.
 */
(function (global) {
  "use strict";

  var WINDOW_URL = "/series/v3.0_last90.json";
  var MAX_STALE_DAYS = 4;          /* older than this -> do not take over the gauge */

  var V3 = {
    active: false,                 /* true only once a fresh, valid window is loaded */
    reason: "not loaded",
    rows: [],
    last: null,
    support: null,
    ready: null
  };

  function daysBetween(aIso, bIso) {
    var a = Date.parse(aIso + "T00:00:00Z"), b = Date.parse(bIso + "T00:00:00Z");
    return Math.round((b - a) / 86400000);
  }

  function todayUTC() {
    return new Date().toISOString().slice(0, 10);
  }

  /* ---- causal empirical CDF, interpolated from the shipped quantile grid ----
   * The grid is F^-1 sampled at `quantile_levels`; F(x) is its inverse, found by
   * binary search plus linear interpolation. Clipped to the same [0.001, 0.999]
   * the model uses, so the browser and the tape agree at the edges. */
  function cdfFromGrid(grid, levels, x) {
    if (!grid || !grid.length || !isFinite(x)) return NaN;
    if (x <= grid[0]) return 0.001;
    if (x >= grid[grid.length - 1]) return 0.999;
    var lo = 0, hi = grid.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (grid[mid] <= x) lo = mid; else hi = mid;
    }
    var span = grid[hi] - grid[lo];
    var f = span > 0 ? (x - grid[lo]) / span : 0;
    var q = levels[lo] + (levels[hi] - levels[lo]) * f;
    return Math.max(0.001, Math.min(0.999, q));
  }

  /* ---- pillar values at a hypothetical price -------------------------------
   * V, G and T are inverted because they are functions of price given today's
   * cost basis and growth path. Sigma is HELD at its last close: it depends on
   * realised volatility, which a hypothetical price level does not determine.
   * That asymmetry is the whole reason the widget is captioned
   * "valuation-implied price" and not "the price at which the model reads X". */
  function pillarsAtPrice(P) {
    var s = V3.support;
    if (!s || !s.last) return null;
    var lv = s.quantile_levels;
    var out = {};

    /* V and G are BLENDS of the expanding and 4-year maps; using the expanding
     * grid alone would make this a different object from the committed pillar. */
    function blended(key, x, w) {
      var fe = cdfFromGrid(s[key], lv, x);
      var f4 = s[key + "_4y"] ? cdfFromGrid(s[key + "_4y"], lv, x) : NaN;
      if (!isFinite(f4)) return fe;
      return w[0] * fe + w[1] * f4;
    }
    var bw = s.blend || { V: [0.65, 0.35], G: [0.70, 0.30] };

    if (s.mvrv && s.realized_price) {
      out.V = blended("mvrv", P / s.realized_price, bw.V);
    }
    if (s.g_raw && s.last.a != null && s.last.b != null) {
      var d = (Date.parse(s.last.asof_date + "T00:00:00Z") -
               Date.parse(s.genesis + "T00:00:00Z")) / 86400000;
      var g = Math.log(P) - (s.last.a + s.last.b * Math.log(d));
      out.G = blended("g_raw", g, bw.G);
    }
    if (s.mayer && V3.mayerSMA) {
      out.T = cdfFromGrid(s.mayer, lv, P / V3.mayerSMA);
    }
    out.S = s.last.S;                       /* held at last close, by design */
    return out;
  }

  /* Blend exactly as compute.py does: weighted over LIVE families, divided by
   * the live weight. The retired 0.10 never enters numerator or denominator. */
  function blend(p) {
    var w = V3.support.weights, num = 0, den = 0;
    for (var k in w) {
      if (p[k] != null && isFinite(p[k])) { num += w[k] * p[k]; den += w[k]; }
    }
    return den > 0 ? num / den : NaN;
  }

  /* The published score is the causal RANK of the smoothed blend, not the blend.
   * Inverting the families gives a blend value, so it must be mapped through the
   * composite's own historical distribution before it can be compared with the
   * gauge. Skipping this step would put blend-space numbers in the matrix while
   * the gauge shows ranks -- two different "current risk" objects on one page,
   * which is defect F in a new costume. */
  function rankOfBlend(b) {
    var s = V3.support;
    if (!s || !s.composite) return b;           /* no grid: degrade to blend */
    return cdfFromGrid(s.composite, s.quantile_levels, b);
  }

  function riskAtPrice(P) {
    var p = pillarsAtPrice(P);
    return p ? rankOfBlend(blend(p)) : NaN;
  }

  /* The un-ranked blend, for diagnostics only. Never shown as "risk". */
  function blendAtPrice(P) {
    var p = pillarsAtPrice(P);
    return p ? blend(p) : NaN;
  }

  /* Monotone in price, so bisect in log space.
   *
   * Returns null when the level is UNREACHABLE by price alone. With Sigma held
   * at its last close, the blend saturates: every price-driven pillar clips at
   * 0.999, so the ceiling is (0.31+0.24+0.15)*0.999 + 0.20*S, all over 0.90.
   * With S = 0.44 that ceiling is about 0.87, and asking for 0.90 would
   * otherwise return an absurd price. Printing "—" is the honest answer: no
   * price reaches that level while the volatility regime is what it is. */
  function priceForRisk(target) {
    if (!V3.active || !V3.support) return NaN;
    var base = V3.last.price_usd, lo = base / 400, hi = base * 400;
    if (!(riskAtPrice(lo) < target)) return null;   /* below the floor, not $192 */
    if (!(riskAtPrice(hi) > target)) return null;   /* above the ceiling */
    for (var i = 0; i < 60; i++) {
      var mid = Math.sqrt(lo * hi);
      if (riskAtPrice(mid) < target) lo = mid; else hi = mid;
    }
    return Math.sqrt(lo * hi);
  }

  /* ---- intraday trend overlay ----------------------------------------------
   * Mayer moved to spot, everything else held. This is NOT the risk number and
   * must never be rendered as one; app.js labels it explicitly. It exists so a
   * reader can see which way the price-sensitive part of the tape is leaning
   * between daily commits. */
  function intradayOverlay(spot) {
    if (!V3.active || !V3.support || !isFinite(spot)) return null;
    var p = pillarsAtPrice(spot);
    if (!p) return null;
    p.V = V3.last.V; p.G = V3.last.G;      /* only the trend leg moves intraday */
    var v = rankOfBlend(blend(p));         /* same scale as the published score */
    return {
      value: v,
      delta: v - V3.last.risk01,
      label: "intraday trend overlay",
      isRisk: false
    };
  }

  function load() {
    V3.ready = fetch(WINDOW_URL, { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("window " + r.status);
        return r.json();
      })
      .then(function (doc) {
        if (!doc || !doc.rows || !doc.rows.length) throw new Error("empty window");
        var last = doc.rows[doc.rows.length - 1];
        var lag = daysBetween(last.asof_date, todayUTC());
        V3.rows = doc.rows;
        V3.last = last;
        V3.support = doc.map_support || null;
        V3.schema = doc.schema_version;
        if (!V3.support || !V3.support.weights) {
          V3.reason = "window carries no map support";
          return V3;
        }
        if (lag > MAX_STALE_DAYS) {
          V3.reason = "tape is " + lag + " days behind (" + last.asof_date + ")";
          return V3;
        }
        /* Mayer needs today's SMA200 denominator; derive it from the last row's
         * own mayer and price so the browser never rebuilds a 200-day average. */
        if (last.mayer) V3.mayerSMA = last.price_usd / last.mayer;
        V3.active = true;
        V3.reason = "ok";
        return V3;
      })
      .catch(function (e) {
        V3.active = false;
        V3.reason = String(e && e.message ? e.message : e);
        return V3;
      });
    return V3.ready;
  }

  /* Highest and lowest risk any price can produce while Sigma is held. */
  function reachableRange() {
    if (!V3.active) return null;
    var base = V3.last.price_usd;
    return { lo: riskAtPrice(base / 400), hi: riskAtPrice(base * 400) };
  }

  V3.rankOfBlend = rankOfBlend;
  V3.blendAtPrice = blendAtPrice;
  V3.reachableRange = reachableRange;
  V3.cdfFromGrid = cdfFromGrid;
  V3.pillarsAtPrice = pillarsAtPrice;
  V3.riskAtPrice = riskAtPrice;
  V3.priceForRisk = priceForRisk;
  V3.intradayOverlay = intradayOverlay;
  V3.load = load;

  global.V3 = V3;
  if (typeof document !== "undefined") load();
})(typeof window !== "undefined" ? window : globalThis);
