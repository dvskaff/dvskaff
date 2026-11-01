"""Rebuilds the profile README graphics from my projects' live results.

Run by .github/workflows/refresh.yml every Monday (and on demand). It downloads the metrics that each
project publishes to GitHub Pages, redraws the SVG cards in assets/, and stamps the
README with the refresh date. Standard library only, so the workflow needs no installs.

    python scripts/build_profile.py            # fetch live results, rebuild everything
    python scripts/build_profile.py --offline  # rebuild from data/latest.json without fetching
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from font_metrics import BOLD, CHARS, REGULAR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS, CACHE, README = ROOT / "assets", ROOT / "data" / "latest.json", ROOT / "README.md"
USER = "dvskaff"
SOURCES = {
    "citibike": f"https://{USER}.github.io/citibike-demand-forecasting/metrics.json",
    "email": f"https://{USER}.github.io/email-campaign-uplift/results.json",
}
FONT = "Helvetica, Arial, sans-serif"

# Every graphic uses this palette, so the profile looks dark whatever theme the visitor's GitHub uses.
THEME = "navy"
THEMES = {
    "navy": dict(bg="#0a1424", card="#0f1c30", border="#24374f", ink="#eef4fb", muted="#9fb3cb",
                 chip="#0a1424", chip_border="#2c4363", blue="#5aa8ff", orange="#f2804f", green="#3fcf6e",
                 violet="#a493ff"),
    "light": dict(bg="#ffffff", card="#f6f8fa", border="#d0d7de", ink="#1f2328", muted="#59636e",
                  chip="#ffffff", chip_border="#d0d7de", blue="#1f6fd1", orange="#d9541f", green="#1a7f37",
                  violet="#5a45c9"),
    "dark": dict(bg="#0d1117", card="#151b23", border="#30363d", ink="#e6edf3", muted="#9198a1",
                 chip="#0d1117", chip_border="#3d444d", blue="#4c9df5", orange="#f2804f", green="#3fb950",
                 violet="#9d8cf2"),
}

TOOLKIT = [
    ("Languages", ["SQL", "Python", "R", "JavaScript", "HTML", "CSS", "VBA"]),
    ("Machine learning", ["scikit-learn", "XGBoost", "LightGBM", "PyTorch", "TensorFlow/Keras", "Hugging Face",
                          "Optuna", "MLflow", "SHAP"]),
    ("Statistics & causal", ["statsmodels", "statsforecast", "SciPy", "EconML", "A/B testing", "CUPED",
                             "Uplift modeling", "Forecasting"]),
    ("Data engineering", ["Snowflake", "dbt", "Apache Airflow", "Databricks", "PySpark", "BigQuery", "DuckDB",
                          "PostgreSQL", "Fivetran", "Hightouch", "Polars", "pandas"]),
    ("Analytics & BI", ["Tableau", "Power BI", "Looker", "Amplitude", "Excel", "Plotly", "D3.js", "Matplotlib"]),
    ("Engineering", ["Git", "GitHub Actions", "GitHub Pages", "GitLab", "pytest", "Flask", "SQLAlchemy", "Jupyter",
                     "Claude Code", "MCP"]),
]


# --------------------------------------------------------------------------- helpers
def text_width(text: str, size: float, bold: bool = False) -> float:
    table = BOLD if bold else REGULAR
    return sum(table[CHARS.index(c)] if c in CHARS else 556 for c in text) * size / 1000


def t(x, y, text, size, color, bold=False, anchor="start", extra=""):
    weight = ' font-weight="700"' if bold else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}"{weight} fill="{color}" '
            f'text-anchor="{anchor}"{extra}>{escape(text)}</text>')


def chip(x, y, label, th, size=12.5, h=26):
    w = text_width(label, size) + 22
    return (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="{h / 2}" fill="{th["chip"]}" '
            f'stroke="{th["chip_border"]}"/>' + t(x + w / 2, y + h / 2 + size * 0.36, label, size, th["ink"],
                                                   anchor="middle")), w


def svg(width, height, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}"><title>{escape(title)}'
            f"</title>{body}</svg>\n")


def wrap(text, size, max_width, bold=False):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if text_width(trial, size, bold) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def fmt_date(iso: str | None) -> str | None:
    return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %-d, %Y") if iso else None


# --------------------------------------------------------------------------- data
def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"{USER}-profile-refresh"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URLs)
        return json.load(r)


def citibike_stats(m: dict) -> dict:
    summary = {row["model"]: row for row in m["summary"]}
    champ = m["champion"]
    baselines = [summary[n]["WAPE"] for n in ("Seasonal naive", "AutoETS", "AutoARIMA") if n in summary]
    best_base = min(baselines)
    cov80 = next((c["coverage"] for c in m.get("coverage", []) if abs(c["level"] - 0.8) < 1e-6), None)
    return {
        "wape": summary[champ]["WAPE"],
        "error_cut": 1 - summary[champ]["WAPE"] / best_base,
        "rides": m["total_rides"],
        "coverage80": cov80,
        "data_end": m.get("data_end"),
    }


def email_stats(r: dict) -> dict:
    eff = {(e["arm"], e["outcome"]): e for e in r["effects"]}
    pol = {p["policy"]: p for p in r["policies"]}
    best = max(r["policies"], key=lambda p: p["incremental_profit"])
    blast = pol["Men's email to everyone"]["incremental_profit"]
    return {
        "customers": r["n_customers"],
        "visit_lift": eff[("Men's email", "visit")]["relative_lift"],
        "profit_gain": best["incremental_profit"] / blast - 1,
    }


def load(offline: bool) -> dict:
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if offline:
        return cached
    out = dict(cached)
    for key, url in SOURCES.items():
        try:
            raw = fetch(url)
            out[key] = citibike_stats(raw) if key == "citibike" else email_stats(raw)
            print(f"fetched {key}: {out[key]}")
        except Exception as exc:  # keep the last good numbers if a site is briefly unavailable
            print(f"WARNING: could not refresh {key} ({exc}); keeping cached values")
    CACHE.write_text(json.dumps(out, indent=2) + "\n")
    return out


# --------------------------------------------------------------------------- graphics
def banner() -> str:
    """One dark banner that reads well on both GitHub themes. The chart on the right is decorative."""
    W, H = 1200, 300
    pts = [(700, 205), (745, 190), (790, 198), (835, 168), (880, 176), (925, 140), (970, 150), (1015, 118),
           (1060, 126), (1105, 92), (1150, 84)]
    split = 7  # history up to here, forecast after
    hist = " ".join(f"{x},{y}" for x, y in pts[: split + 1])
    fc = " ".join(f"{x},{y}" for x, y in pts[split:])
    upper = [(x, y - 3 - 11 * i) for i, (x, y) in enumerate(pts[split:])]
    lower = [(x, y + 3 + 11 * i) for i, (x, y) in enumerate(pts[split:])]
    band = " ".join(f"{x},{y}" for x, y in upper + lower[::-1])
    grid = "".join(f'<line x1="680" x2="1170" y1="{y}" y2="{y}" stroke="#ffffff" stroke-opacity="0.07"/>'
                   for y in (80, 120, 160, 200, 240))
    ex, ey = pts[-1]
    body = f"""
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0a1a33"/>
    <stop offset="1" stop-color="#114a8f"/></linearGradient>
  <style>
    .draw {{ stroke-dasharray: 900; stroke-dashoffset: 900; animation: draw 2.2s ease-out forwards; }}
    .fc {{ stroke-dasharray: 6 6; opacity: 0; animation: fade .6s ease-out 2.1s forwards; }}
    .band {{ opacity: 0; animation: fade .8s ease-out 2.1s forwards; }}
    .dot {{ opacity: 0; animation: fade .3s ease-out 2.6s forwards, pulse 2.4s ease-in-out 3s infinite; }}
    @keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
    @keyframes fade {{ to {{ opacity: 1; }} }}
    @keyframes pulse {{ 0%,100% {{ r: 6; }} 50% {{ r: 9; }} }}
  </style>
</defs>
<rect width="{W}" height="{H}" rx="18" fill="url(#bg)"/>
{grid}
<polygon class="band" points="{band}" fill="#eb6834" fill-opacity="0.18"/>
<polyline class="draw" points="{hist}" fill="none" stroke="#8cc4ff" stroke-width="3" stroke-linejoin="round"
  stroke-linecap="round"/>
<polyline class="fc" points="{fc}" fill="none" stroke="#f2804f" stroke-width="3" stroke-linecap="round"/>
<circle class="dot" cx="{ex}" cy="{ey}" r="6" fill="#f2804f"/>
{t(1060, 64, "forecast", 14, "#f2b597", anchor="middle")}
{t(70, 118, "David Skaff", 58, "#ffffff", bold=True)}
{t(72, 166, "Data Science · Analytics Engineering · Experimentation", 22, "#cfe3fb")}
{t(72, 214, "MSc Business Analytics, Ivey Business School", 19, "#9dbde3")}
{t(72, 244, "Data Analyst Intern, Super.com (2026) · Ontario, Canada", 19, "#9dbde3")}
"""
    return svg(W, H, body, "David Skaff: Data Science, Analytics Engineering, Experimentation")


def badge(label: str, value: str, accent: str) -> str:
    size, h = 13, 30
    lw, vw = text_width(label, size, True) + 24, text_width(value, size) + 24
    W = lw + vw
    body = (f'<rect width="{W:.1f}" height="{h}" rx="8" fill="#0d1f3a"/>'
            f'<path d="M{lw:.1f} 0H{W - 8:.1f}a8 8 0 0 1 8 8v{h - 16}a8 8 0 0 1-8 8H{lw:.1f}z" fill="{accent}"/>'
            + t(lw / 2, 19.5, label, size, "#ffffff", bold=True, anchor="middle")
            + t(lw + vw / 2, 19.5, value, size, "#ffffff", anchor="middle"))
    return svg(round(W), h, body, f"{label}: {value}")


def project_card(th: dict, *, tag, tag_color, title, blurb, stats, chips, footer, live=True) -> str:
    """Full-width card: story on the left, three live numbers on the right."""
    W, H, P, split = 880, 250, 28, 520
    parts = [f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{th["card"]}" '
             f'stroke="{th["border"]}"/>',
             "<style>.live{animation:p 2s ease-in-out infinite}@keyframes p{50%{opacity:.3}}</style>"]
    tw = text_width(tag, 11.5, True) + 20
    parts += [f'<rect x="{P}" y="{P}" width="{tw:.1f}" height="22" rx="11" fill="{tag_color}" fill-opacity="0.14"/>',
              t(P + tw / 2, P + 15.2, tag, 11.5, tag_color, bold=True, anchor="middle")]
    if live:
        lx = P + tw + 14
        parts += [f'<circle class="live" cx="{lx + 4}" cy="{P + 11}" r="4.5" fill="{th["green"]}"/>',
                  t(lx + 14, P + 15.2, "LIVE", 11.5, th["green"], bold=True)]
    parts.append(t(P, P + 56, title, 23, th["ink"], bold=True))
    for i, line in enumerate(wrap(blurb, 14.5, split - 2 * P)):
        parts.append(t(P, P + 84 + 21 * i, line, 14.5, th["muted"]))
    cx = P
    for c in chips:
        s, w = chip(cx, H - P - 54, c, th, size=12.5)
        parts.append(s)
        cx += w + 8
    parts.append(t(P, H - P + 2, footer, 12.5, th["muted"]))
    parts.append(f'<line x1="{split}" x2="{split}" y1="{P}" y2="{H - P}" stroke="{th["border"]}"/>')
    row = (H - 2 * P) / len(stats)
    for i, (value, label, color) in enumerate(stats):
        y = P + row * i + row / 2
        parts.append(t(split + 30, y + 11, value, 31, th[color], bold=True))
        lines = wrap(label, 13.5, W - P - (split + 150))
        for j, line in enumerate(lines):
            parts.append(t(split + 150, y + 5 - 9 * (len(lines) - 1) + 18 * j, line, 13.5, th["muted"]))
    return svg(W, H, "".join(parts), title)


def toolkit(th: dict) -> str:
    W, P, label_w, gap, row_h = 880, 26, 172, 7, 36
    parts, y = [], P
    for label, items in TOOLKIT:
        x = P + label_w
        parts.append(t(P, y + 18.5, label.upper(), 11.5, th["muted"], bold=True, extra=' letter-spacing="0.6"'))
        for item in items:
            w = text_width(item, 13) + 22
            if x + w > W - P:
                x, y = P + label_w, y + row_h
            s, w = chip(x, y, item, th, size=13, h=28)
            parts.append(s)
            x += w + gap
        y += row_h + 12
    H = y + P - 12
    frame = (f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{th["card"]}" '
             f'stroke="{th["border"]}"/>')
    names = ", ".join(i for _, items in TOOLKIT for i in items)
    return svg(W, H, frame + "".join(parts), f"Toolkit: {names}")


# --------------------------------------------------------------------------- build
def build(stats: dict) -> None:
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "banner.svg").write_text(banner())
    (ASSETS / "badge-linkedin.svg").write_text(badge("LinkedIn", "davidskaff1", "#0a66c2"))
    (ASSETS / "badge-available.svg").write_text(badge("Available", "December 2026", "#1a7f37"))
    (ASSETS / "badge-location.svg").write_text(badge("Work", "Canada & U.S. (TN)", "#d9541f"))

    cb, em = stats["citibike"], stats["email"]
    cb_footer = (f"Data through {fmt_date(cb['data_end'])} · retrains monthly" if cb.get("data_end")
                 else "Retrains itself every month")
    for th in (THEMES[THEME],):
        (ASSETS / "card-citibike.svg").write_text(project_card(
            th, tag="TIME-SERIES ML", tag_color=th["blue"], title="NYC Citi Bike Demand Forecasting",
            blurb="Forecasts daily ridership 30 days ahead from every public trip and daily weather; "
                  "retrained and redeployed by GitHub Actions.",
            stats=[(f"{cb['wape']:.1%}", "forecast error (WAPE), 30 days ahead", "blue"),
                   (f"−{cb['error_cut']:.0%}", "error vs. best statistical baseline", "orange"),
                   (f"{cb['rides'] / 1e6:.0f}M", "trips analyzed with DuckDB", "violet")],
            chips=["LightGBM", "Optuna", "MLflow", "SHAP", "DuckDB"], footer=cb_footer))
        (ASSETS / "card-email.svg").write_text(project_card(
            th, tag="CAUSAL INFERENCE", tag_color=th["orange"], title="Email Experiment & Uplift Modeling",
            blurb="Did a marketing email work, for whom, and who should get one next? A/B testing plus "
                  "causal-forest uplift models and a targeting simulator.",
            stats=[(f"{em['customers'] / 1000:.0f}K", "customers in a randomized experiment", "blue"),
                   (f"+{em['visit_lift']:.0%}", "site visits from the best email", "orange"),
                   (f"+{em['profit_gain']:.0%}", "est. profit from targeting vs. emailing all", "violet")],
            chips=["EconML", "LightGBM", "SHAP", "statsmodels", "DuckDB"],
            footer="Analysis reruns on every push"))
        (ASSETS / "toolkit.svg").write_text(toolkit(th))

    stamp = date.today().strftime("%b %-d, %Y")
    text = README.read_text()
    text = re.sub(r"(<!-- REFRESHED -->).*?(<!-- /REFRESHED -->)", lambda m: m.group(1) + stamp + m.group(2), text)
    README.write_text(text)
    print(f"built {len(list(ASSETS.glob('*.svg')))} graphics, stamped {stamp} at {datetime.now(UTC):%H:%M} UTC")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip fetching; rebuild from data/latest.json")
    build(load(parser.parse_args().offline))
