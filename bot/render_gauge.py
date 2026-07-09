#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# repo path: bot/render_gauge.py
"""
render_gauge.py — daily gauge card for the X post (1200x675, 16:9).

Draws the dashboard's risk gauge (exact RISK_STOPS ramp from index.html) with
the needle at TODAY's value, the score big, a neutral level chip, the 365-day
stat, the data date and bitcoinrisk.net. Pillow-only (no system deps beyond
the wheels), supersampled 2x for clean anti-aliasing.

Fonts: Space Grotesk (variable, Bold instance) + IBM Plex Mono fetched at
runtime; the top-left brand uses a Helvetica-metric font (Liberation Sans,
present on GitHub runners); all fall back to DejaVu.

Extreme readings (LOW < 0.30 or HIGH >= 0.80) get a colored edge glow in the
level color plus a neutral "EXTREME" badge, so milestone days stand out when
shared. The "as of" date is the DATA date (model.lastDate) formatted like the
tweet, e.g. "Jul 7" — not the render date; a 1-2 day Coin Metrics lag is
normal and this deliberately matches the dashboard's own "as of" reading.
"""
import io
import math
import os
import datetime as dt

import requests
from PIL import Image, ImageDraw, ImageFont

W, H, SS = 1200, 675, 2                      # canvas + supersample factor

STOPS = [(0.00, (30, 111, 235)), (0.22, (70, 179, 201)), (0.48, (232, 200, 74)),
         (0.70, (236, 122, 28)), (0.88, (214, 61, 46)), (1.00, (200, 46, 52))]

# Readings at the low/high ends of the scale get the "extreme" treatment
# (colored glow frame + badge) to make milestone days more shareable.
EXTREME_LOW, EXTREME_HIGH = 0.30, 0.80

FONT_URLS = {
    "grotesk": "https://raw.githubusercontent.com/google/fonts/main/ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
    "mono": "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-mono/fonts/complete/ttf/IBMPlexMono-SemiBold.ttf",
}
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
# Real Helvetica is proprietary and not installable; Liberation Sans is the
# standard metric-compatible equivalent (present on GitHub runners too).
HELVETICA_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _fmt_date(iso: str) -> str:
    """ISO 'YYYY-MM-DD' -> 'Jul 7' (matches the tweet text). Falls back to raw."""
    try:
        d = dt.date.fromisoformat(iso)
        return d.strftime("%b %d").replace(" 0", " ")
    except Exception:
        return iso


def risk_color(v):
    v = max(0.0, min(1.0, v))
    for (a, ca), (b, cb) in zip(STOPS, STOPS[1:]):
        if v <= b:
            t = 0 if b == a else (v - a) / (b - a)
            return tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return STOPS[-1][1]


def _font_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fonts")
    os.makedirs(d, exist_ok=True)
    return d


def _fetch(name, url):
    path = os.path.join(_font_dir(), name + ".ttf")
    if not os.path.exists(path):
        try:
            r = requests.get(url, timeout=12)
            r.raise_for_status()
            open(path, "wb").write(r.content)
        except Exception as e:
            print(f"[gauge] font fetch {name} failed: {e.__class__.__name__}")
            return None
    return path


def _load(kind, size):
    """kind: 'display' | 'mono' | 'helvetica'. Returns the best available font."""
    if kind == "helvetica":
        for p in HELVETICA_CANDIDATES:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
        return ImageFont.truetype(DEJAVU, size)
    if kind == "display":
        p = _fetch("grotesk", FONT_URLS["grotesk"])
        if p:
            try:
                f = ImageFont.truetype(p, size)
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
                return f
            except Exception:
                pass
        return ImageFont.truetype(DEJAVU, size)
    p = _fetch("mono", FONT_URLS["mono"])
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.truetype(DEJAVU_MONO if os.path.exists(DEJAVU_MONO) else DEJAVU, size)


def _pol(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _has_glyph(font, ch):
    try:
        l, t, r, b = font.getbbox(ch)
        return (r - l) > 0
    except Exception:
        return False


def _draw_extreme_frame(img, level_col):
    """Composite a soft edge glow + bold border in the level color (extreme flag).
    Resolution-relative so it can run at final size."""
    from PIL import ImageFilter
    w, h = img.size
    s = w / 1200.0
    band, rad = max(6, int(14 * s)), int(30 * s)
    box = [band, band, w - 1 - band, h - 1 - band]
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(box, radius=rad,
                                           outline=level_col + (255,), width=band)
    glow = glow.filter(ImageFilter.GaussianBlur(int(11 * s)))
    out = img.convert("RGBA")
    out = Image.alpha_composite(out, glow)
    out = Image.alpha_composite(out, glow)          # accumulate for a visible halo
    ImageDraw.Draw(out).rounded_rectangle(box, radius=rad,
                                          outline=level_col + (255,),
                                          width=max(2, int(4 * s)))
    return out.convert("RGB")


def render(risk: float, level: str, hist_line: str, date_str: str,
           out_path: str) -> str:
    """Render the card; returns out_path."""
    w, h = W * SS, H * SS
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)

    # background vertical gradient + faint gridlines
    top, bot = (13, 20, 32), (9, 13, 20)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)],
               fill=tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    for gy in range(120 * SS, h, 110 * SS):
        d.line([(0, gy), (w, gy)], fill=(21, 29, 43), width=1)

    # ---- gauge (left) ------------------------------------------------------
    cx, cy = 400 * SS, 352 * SS
    r_mid, ring_w = 232 * SS, 46 * SS
    a0, a1 = 135, 405
    bbox = [cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid]
    n = 160
    for i in range(n):
        v0, v1 = i / n, (i + 1) / n
        col = risk_color((v0 + v1) / 2)
        d.arc(bbox, a0 + (a1 - a0) * v0, a0 + (a1 - a0) * v1 + 0.6,
              fill=col, width=ring_w)
    for ang, col in ((a0, risk_color(0)), (a1, risk_color(1))):   # round caps
        px, py = _pol(cx, cy, r_mid, ang)
        rr = ring_w / 2
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=col)

    mono_s = _load("mono", 26 * SS)
    for v, lbl, anchor in ((0.0, "0", "rm"), (1.0, "1", "lm")):
        ang = a0 + (a1 - a0) * v
        px, py = _pol(cx, cy, r_mid + ring_w, ang)
        d.text((px, py), lbl, font=mono_s, fill=(143, 161, 179), anchor=anchor)

    # coin hub with ₿
    coin_r = 96 * SS
    for i in range(coin_r, 0, -1):
        t = i / coin_r
        col = tuple(round((255, 197, 104)[k] * t + (201, 117, 7)[k] * (1 - t))
                    for k in range(3))
        d.ellipse([cx - i - 0.18 * coin_r * (1 - t), cy - i - 0.22 * coin_r * (1 - t),
                   cx + i - 0.18 * coin_r * (1 - t), cy + i - 0.22 * coin_r * (1 - t)],
                  fill=col)
    d.ellipse([cx - coin_r, cy - coin_r, cx + coin_r, cy + coin_r],
              outline=(143, 83, 5), width=3 * SS)
    fb = _load("display", int(coin_r * 1.42))
    glyph_img = Image.new("RGBA", (coin_r * 3, coin_r * 3), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph_img)
    gcx = gcy = coin_r * 1.5
    if _has_glyph(fb, "\u20bf"):
        gd.text((gcx, gcy), "\u20bf", font=fb, fill=(255, 248, 238), anchor="mm")
    else:                                          # constructed ₿ fallback
        gd.text((gcx, gcy), "B", font=fb, fill=(255, 248, 238), anchor="mm")
        bw, bh = int(coin_r * 0.13), int(coin_r * 0.30)
        for dx in (-int(coin_r * 0.16), int(coin_r * 0.16)):
            gd.rectangle([gcx + dx - bw / 2, gcy - coin_r * 0.92,
                          gcx + dx + bw / 2, gcy - coin_r * 0.92 + bh],
                         fill=(255, 248, 238))
            gd.rectangle([gcx + dx - bw / 2, gcy + coin_r * 0.92 - bh,
                          gcx + dx + bw / 2, gcy + coin_r * 0.92],
                         fill=(255, 248, 238))
    glyph_img = glyph_img.rotate(14, resample=Image.BICUBIC)
    img.paste(glyph_img, (int(cx - coin_r * 1.5), int(cy - coin_r * 1.5)), glyph_img)

    # needle at the live value
    ang = a0 + (a1 - a0) * max(0.0, min(1.0, risk))
    tipx, tipy = _pol(cx, cy, r_mid + ring_w * 0.62, ang)
    perp = ang + 90
    bx1, by1 = _pol(cx, cy, coin_r + 8 * SS, ang)
    ox, oy = math.cos(math.radians(perp)) * 7 * SS, math.sin(math.radians(perp)) * 7 * SS
    d.polygon([(tipx, tipy), (bx1 + ox, by1 + oy), (bx1 - ox, by1 - oy)],
              fill=(244, 247, 251), outline=(12, 17, 24))

    # ---- text block (right) ------------------------------------------------
    x0 = 745 * SS
    col = risk_color(risk)
    is_extreme = (risk < EXTREME_LOW or risk >= EXTREME_HIGH) \
        and os.environ.get("EXTREME_STYLE", "on").strip().lower() != "off"

    d.text((x0, 150 * SS), "BTC RISK", font=_load("mono", 34 * SS),
           fill=(143, 161, 179))
    d.text((x0 - 6 * SS, 200 * SS), f"{risk:.2f}",
           font=_load("display", 175 * SS), fill=col)

    chip_f = _load("display", 40 * SS)
    tl, tt, tr, tb = d.textbbox((0, 0), level, font=chip_f)
    pad, cy0 = 22 * SS, 425 * SS
    d.rounded_rectangle([x0, cy0, x0 + (tr - tl) + 2 * pad, cy0 + (tb - tt) + 2 * pad],
                        radius=14 * SS,
                        fill=tuple(int(c * 0.22) for c in col), outline=col, width=2 * SS)
    d.text((x0 + pad, cy0 + pad - tt), level, font=chip_f, fill=col)

    # historical stat: wrap to two lines; sits bottom-right (brand no longer here)
    hist_f = _load("mono", 29 * SS)
    words = hist_line.split()
    mid = len(hist_line) // 2
    split_i, acc = len(words), 0
    for i, wd in enumerate(words):
        acc += len(wd) + 1
        if acc >= mid:
            split_i = i + 1
            break
    l1, l2 = " ".join(words[:split_i]), " ".join(words[split_i:])
    d.text((x0, 522 * SS), l1, font=hist_f, fill=(200, 210, 220))
    if l2:
        d.text((x0, 562 * SS), l2, font=hist_f, fill=(200, 210, 220))
    d.text((x0, 615 * SS), f"as of {_fmt_date(date_str)}",
           font=_load("mono", 27 * SS), fill=(120, 134, 150))

    img = img.resize((W, H), Image.LANCZOS)

    # ---- overlays at final resolution (crisp text) -------------------------
    if is_extreme:
        img = _draw_extreme_frame(img, col)
    d = ImageDraw.Draw(img)

    # brand: top-left, Helvetica-equivalent, larger
    d.text((34, 30), "bitcoinrisk.net", font=_load("helvetica", 44),
           fill=(232, 200, 74))

    # extreme badge: top-right pill, neutral wording (no buy/sell implication)
    if is_extreme:
        badge_f = _load("display", 26)
        label = "EXTREME"
        bl, bt, br, bb = d.textbbox((0, 0), label, font=badge_f)
        bw, bh = (br - bl), (bb - bt)
        px1, py1 = W - 34, 30
        pad_b = 14
        d.rounded_rectangle([px1 - bw - 2 * pad_b, py1, px1, py1 + bh + 2 * pad_b],
                            radius=(bh + 2 * pad_b) // 2, fill=col)
        d.text((px1 - pad_b - bw, py1 + pad_b - bt), label, font=badge_f,
               fill=(12, 17, 24))

    img.save(out_path, "PNG", optimize=True)
    print(f"[gauge] wrote {out_path} ({os.path.getsize(out_path)//1024} KB"
          f"{', EXTREME' if is_extreme else ''})")
    return out_path


if __name__ == "__main__":
    render(0.62, "ELEVATED", "Higher than 74% of the last 365 days",
           "2026-07-07", "/tmp/gauge_normal.png")
    render(0.08, "LOW", "Lowest reading in 300 days",
           "2026-07-07", "/tmp/gauge_extreme.png")
