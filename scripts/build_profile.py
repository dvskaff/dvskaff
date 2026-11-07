"""Rebuilds the profile README graphics from my projects' live results.

Run by .github/workflows/refresh.yml every Monday (and on demand). It downloads the results that each
project publishes to GitHub Pages, redraws one SVG card per project in assets/, rewrites the project grid and the
"which project fits which role" table in the README, and stamps the refresh date. Standard library only, so the
workflow needs no installs. If a project's site is briefly unavailable, its card keeps the last good numbers.

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

TOOLKIT = [  # matches the skills on my resume; tools are added here once a project uses them
    ("Languages", ["SQL", "Python", "R", "JavaScript", "HTML", "CSS", "VBA"]),
    ("Machine learning & AI", ["scikit-learn", "XGBoost", "LightGBM", "PyTorch", "TensorFlow/Keras",
                               "Hugging Face Transformers", "llama.cpp", "Optuna", "SHAP", "Fairlearn"]),
    ("Statistics & causal", ["statsmodels", "statsforecast", "SciPy", "lifelines", "EconML", "A/B testing",
                             "CUPED", "Uplift modeling", "Survival analysis", "Forecasting"]),
    ("MLOps & serving", ["MLflow", "Evidently", "ONNX Runtime", "Docker", "FastAPI", "Flask",
                         "GitHub Actions (CI/CD)"]),
    ("Data engineering", ["PySpark", "Spark SQL", "Delta Lake", "Apache Airflow", "dbt", "Databricks", "Snowflake",
                          "BigQuery", "PostgreSQL", "DuckDB", "SQLite", "Parquet", "SQLAlchemy", "Fivetran",
                          "Hightouch"]),
    ("Data & visualization", ["pandas", "Polars", "NumPy", "Matplotlib", "Seaborn", "Plotly", "hvPlot", "D3.js",
                              "Leaflet", "Folium", "BeautifulSoup", "Splinter"]),
    ("Analytics & BI", ["Tableau", "Power BI", "Looker", "Amplitude", "Excel", "Smartsheet"]),
    ("Engineering", ["Git", "GitHub", "GitLab", "pytest", "Jupyter", "Claude Code", "MCP"]),
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



def month(iso: str | None) -> str | None:
    return datetime.strptime(iso[:7], "%Y-%m").strftime("%b %Y") if iso else None


# --------------------------------------------------------------------------- data
def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"{USER}-profile-refresh"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URLs)
        return json.load(r)


def citibike_stats(m: dict) -> dict:
    summary = {row["model"]: row for row in m["summary"]}
    champ = m["champion"]
    baselines = [summary[n]["WAPE"] for n in ("Seasonal naive", "AutoETS", "AutoARIMA") if n in summary]
    return {"wape": summary[champ]["WAPE"], "error_cut": 1 - summary[champ]["WAPE"] / min(baselines),
            "rides": m["total_rides"], "data_end": m.get("data_end")}  # fmt: skip


def email_stats(r: dict) -> dict:
    eff = {(e["arm"], e["outcome"]): e for e in r["effects"]}
    pol = {p["policy"]: p for p in r["policies"]}
    best = max(r["policies"], key=lambda p: p["incremental_profit"])
    blast = pol["Men's email to everyone"]["incremental_profit"]
    return {"customers": r["n_customers"], "visit_lift": eff[("Men's email", "visit")]["relative_lift"],
            "profit_gain": best["incremental_profit"] / blast - 1}  # fmt: skip


def chicago311_stats(r: dict) -> dict:
    d = r["dbt"]
    return {"warehouse": r["requests_in_warehouse"], "requests_12m": r["requests"],
            "median_days": r["median_days_to_close"], "tests": d["data_tests"] + d["unit_tests"],
            "data_end": str(r["data_as_of"])[:10]}  # fmt: skip


def community_stats(r: dict) -> dict:
    fast = next(e for e in r["experience"] if e["signal"] == "first_q_answered_fast")
    return {"came_back": r["newcomer_came_back_rate"], "fast_answer_pts": fast["diff"],
            "contributions": r["contributions_all_time"], "data_end": str(r["data_end"])[:10]}  # fmt: skip


def mortgage_stats(r: dict) -> dict:
    main = next(m for m in r["metrics"] if m["model"] == "LightGBM, monotonic + calibrated")
    h = r["headline"]
    return {"auc": main["roc_auc"], "applications": r["n_applications"], "group": h["group"],
            "odds_raw": h["odds_ratio_unadjusted"], "odds_adj": h["odds_ratio_adjusted"]}  # fmt: skip


def flights_stats(r: dict) -> dict:
    return {"flights": r["lake"]["silver/flights"]["rows"], "late_after_60": r["late_after_60"],
            "on_time": r["last12"]["on_time_rate"], "data_end": r["window"][1]}  # fmt: skip


def ontario_stats(r: dict) -> dict:
    bt = r["backtest"]
    return {"mape": bt["model"]["mape"], "naive_mape": bt["naive_week"]["mape"],
            "error_cut": bt["improvement_vs_naive"], "data_end": r["data_end"]}  # fmt: skip


def filings_stats(r: dict) -> dict:
    g = r["generation"]
    return {"recall5": r["retrieval_best"]["recall@5"], "bm25_recall5": r["retrieval_bm25"]["recall@5"],
            "correct_rag": g["RAG (single filing)"]["correct"],
            "correct_closed": g["Closed book (no retrieval)"]["correct"], "documents": r["documents"]}  # fmt: skip


def site(repo: str) -> str:
    return f"https://{USER}.github.io/{repo}/"


# Role fit, in the order the table lists roles.
ROLES = ["Data Analyst", "Analytics Engineer", "Data Engineer", "Data Scientist", "ML Engineer"]


def P(key, repo, file, stats, roles, **card):
    return {"key": key, "repo": repo, "url": site(repo) + file, "stats_fn": stats, "roles": roles, "card": card}


# The grid shows the projects in this order, two per row. "card" is filled from the live numbers in build().
PROJECTS = [
    P("ontario", "ontario-demand-forecasting", "results.json", ontario_stats, ["ML Engineer"],
      tag="MLOPS", color="blue", title="Ontario Electricity Demand: Production ML System",
      blurb="How much power will Ontario need tomorrow, hour by hour? Daily forecasts with a model registry, "
            "drift monitoring and an API.",
      chips=["LightGBM", "MLflow", "ONNX", "FastAPI", "Evidently"], link="Live forecast"),
    P("filings", "filings-rag-eval", "results.json", filings_stats, ["ML Engineer"],
      tag="LLM · RAG", color="violet", title="Financial Filings Q&A: RAG & LLM Evaluation",
      blurb="Can an AI answer analysts' questions about SEC filings, cite its sources, and say \"I don't know\" "
            "instead of guessing? Measured on FinanceBench.",
      chips=["llama.cpp", "Embeddings", "BM25", "FastAPI", "Docker"], link="Live evaluation"),
    P("mortgage", "mortgage-fair-lending-model", "results.json", mortgage_stats, ["Data Scientist"],
      tag="CREDIT RISK · FAIRNESS", color="orange", title="Mortgage Denial Modeling & Fair-Lending Audit",
      blurb="Which mortgage applications get denied, and are some groups denied more after accounting for "
            "credit factors? Public HMDA data for Georgia.",
      chips=["LightGBM", "Scorecard", "Fairlearn", "SHAP", "statsmodels"], link="Live dashboard"),
    P("email", "email-campaign-uplift", "results.json", email_stats, ["Data Analyst", "Data Scientist"],
      tag="CAUSAL INFERENCE", color="orange", title="Email Campaign Experiment & Uplift Modeling",
      blurb="Did a marketing email work, for whom, and who should get one next? A/B testing, causal-forest "
            "uplift models and a targeting simulator.",
      chips=["EconML", "LightGBM", "SHAP", "statsmodels", "DuckDB"], link="Live report & simulator"),
    P("flights", "flight-delay-lakehouse", "results.json", flights_stats, ["Data Engineer"],
      tag="DATA ENGINEERING", color="green", title="Flight Delay Lakehouse: PySpark & Delta Lake",
      blurb="Which airlines, airports and hours run late, and how do delays spread from flight to flight? "
            "A medallion lakehouse orchestrated by Airflow.",
      chips=["PySpark", "Delta Lake", "Airflow", "pytest", "Plotly"], link="Live dashboard"),
    P("chicago311", "chicago-311-warehouse", "results.json", chicago311_stats,
      ["Analytics Engineer", "Data Engineer"],
      tag="ANALYTICS ENGINEERING", color="green", title="Chicago 311 Analytics Warehouse: dbt & DuckDB",
      blurb="How fast does Chicago resolve 311 service requests, by department and neighborhood? Tested and "
            "loaded daily.",
      chips=["dbt", "DuckDB", "SQL", "Python", "Plotly"], link="Live dashboard"),
    P("community", "community-retention-analytics", "results.json", community_stats,
      ["Data Analyst", "Analytics Engineer"],
      tag="PRODUCT ANALYTICS", color="violet", title="Community Retention & Product Analytics",
      blurb="Which first experiences make new members of a Q&A community come back? Retention, cohorts and "
            "survival analysis on Stack Exchange data, modeled in dbt.",
      chips=["dbt", "DuckDB", "lifelines", "Cohorts", "SQL"], link="Live dashboard"),
    P("citibike", "citibike-demand-forecasting", "metrics.json", citibike_stats, ["Data Scientist"],
      tag="TIME-SERIES ML", color="blue", title="NYC Citi Bike Demand Forecasting",
      blurb="How many Citi Bike trips will New Yorkers take each day next month? Forecasts from every public trip "
            "and the weather, retrained by GitHub Actions.",
      chips=["LightGBM", "Optuna", "MLflow", "SHAP", "DuckDB"], link="Live dashboard"),
]


def load(offline: bool) -> dict:
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if offline:
        return cached
    out = dict(cached)
    for p in PROJECTS:
        try:
            raw = fetch(p["url"])
            if raw.get("synthetic"):
                raise ValueError("site shows synthetic test data")
            out[p["key"]] = p["stats_fn"](raw)
            print(f"fetched {p['key']}: {out[p['key']]}")
        except Exception as exc:  # keep the last good numbers if a site is briefly unavailable or changed
            print(f"WARNING: could not refresh {p['key']} ({exc!r}); keeping cached values")
    CACHE.write_text(json.dumps(out, indent=2) + "\n")
    return out


def card_numbers(key: str, s: dict) -> tuple[list[tuple[str, str]], str]:
    """The two headline numbers on each card, and its footer (how it stays up to date)."""
    if key == "ontario":
        return ([(f"{s['mape']:.1f}%", "forecast error (MAPE) for the next day"),
                 (f"−{s['error_cut']:.0%}", f"error vs. same hour last week ({s['naive_mape']:.1f}%)")],
                f"Forecasts daily, retrains weekly · data through {fmt_date(s['data_end'])}")
    if key == "filings":
        return ([(f"{s['recall5']:.0%}", f"of questions find the evidence page in the top 5 (BM25: "
                                         f"{s['bm25_recall5']:.0%})"),
                 (f"{s['correct_rag']:.0%}", f"answered correctly with retrieval vs. {s['correct_closed']:.0%} "
                                             "without")],
                f"{s['documents']} SEC filings · re-evaluated on every code change")
    if key == "mortgage":
        return ([(f"{s['auc']:.2f}", f"ROC AUC on a held-out year ({s['applications'] / 1000:.0f}K applications)"),
                 (f"{s['odds_raw']:.1f}×", f"{s['group']} vs. non-Hispanic White denial odds "
                                           f"({s['odds_adj']:.1f}× after credit factors)")],
                "Checks for new HMDA data monthly")
    if key == "email":
        return ([(f"+{s['visit_lift']:.0%}", f"site visits from the best email ({s['customers'] / 1000:.0f}K "
                                            "customers)"),
                 (f"+{s['profit_gain']:.0%}", "est. profit from targeting vs. emailing everyone")],
                "Analysis reruns on every push")
    if key == "flights":
        return ([(f"{s['flights'] / 1e6:.1f}M", "U.S. flights loaded into Delta Lake tables"),
                 (f"{s['late_after_60']:.0%}", "of next flights leave late after a 60+ min late arrival")],
                f"Airflow loads new months weekly · data through {month(s['data_end'])}")
    if key == "chicago311":
        return ([(f"{s['warehouse'] / 1e6:.1f}M", f"requests in the warehouse ({s['requests_12m'] / 1000:.0f}K "
                                                 "in the last year)"),
                 (f"{s['median_days']:.1f} days", "median time to close a service request")],
                f"Loads new data and runs {s['tests']} dbt tests daily")
    if key == "community":
        return ([(f"{s['came_back']:.0%}", "of newcomers come back within 30 days"),
                 (f"+{s['fast_answer_pts'] * 100:.1f} pts", "return rate when the first question is answered "
                                                            "within 24 h")],
                f"{s['contributions'] / 1e6:.1f}M contributions · data through {month(s['data_end'])}")
    if key == "citibike":
        return ([(f"{s['wape']:.1%}", "forecast error (WAPE), 30 days ahead"),
                 (f"−{s['error_cut']:.0%}", "error vs. the best statistical baseline")],
                f"Retrains monthly · data through {fmt_date(s['data_end'])}")
    raise KeyError(key)


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
{t(72, 166, "Data Science · Machine Learning · Data & Analytics Engineering", 20, "#cfe3fb")}
{t(72, 214, "MSc Business Analytics, Ivey Business School", 19, "#9dbde3")}
{t(72, 244, "Data Analyst Intern, Super.com (2026)", 19, "#9dbde3")}
"""
    return svg(W, H, body, "David Skaff: Data Science, Machine Learning, Data and Analytics Engineering")


def badge(label: str, value: str, accent: str) -> str:
    size, h = 13, 30
    lw, vw = text_width(label, size, True) + 24, text_width(value, size) + 24
    W = lw + vw
    body = (f'<rect width="{W:.1f}" height="{h}" rx="8" fill="#0d1f3a"/>'
            f'<path d="M{lw:.1f} 0H{W - 8:.1f}a8 8 0 0 1 8 8v{h - 16}a8 8 0 0 1-8 8H{lw:.1f}z" fill="{accent}"/>'
            + t(lw / 2, 19.5, label, size, "#ffffff", bold=True, anchor="middle")
            + t(lw + vw / 2, 19.5, value, size, "#ffffff", anchor="middle"))
    return svg(round(W), h, body, f"{label}: {value}")



CARD_W, CARD_H = 440, 318


def fit(text: str, size: float, max_width: float, max_lines: int) -> list[str]:
    """Wrap text into at most max_lines. Numbers come from live data, so a longer label must never break the card:
    drop a trailing "(...)" or " · ..." part first, then cut the last line with an ellipsis."""
    lines = wrap(text, size, max_width)
    if len(lines) <= max_lines:
        return lines
    shorter = re.sub(r"\s*(\([^)]*\)|·[^·]*)$", "", text)
    if shorter != text:
        return fit(shorter, size, max_width, max_lines)
    lines = lines[:max_lines]
    while lines[-1] and text_width(lines[-1] + "…", size) > max_width:
        lines[-1] = lines[-1][:-1]
    lines[-1] = lines[-1].rstrip() + "…"
    return lines


def project_card(th: dict, *, tag, color, title, blurb, stats, chips, footer, live=True) -> str:
    """Half-width card (two per row): tag, title, the business question, two live numbers, tools, freshness."""
    W, H, P = CARD_W, CARD_H, 22
    inner = W - 2 * P
    parts = [f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" fill="{th["card"]}" '
             f'stroke="{th["border"]}"/>',
             "<style>.live{animation:p 2s ease-in-out infinite}@keyframes p{50%{opacity:.3}}</style>"]
    accent = th[color]
    tw = text_width(tag, 10.5, True) + 18
    parts += [f'<rect x="{P}" y="{P}" width="{tw:.1f}" height="20" rx="10" fill="{accent}" fill-opacity="0.14"/>',
              t(P + tw / 2, P + 14, tag, 10.5, accent, bold=True, anchor="middle")]
    if live:
        lx = P + tw + 12
        parts += [f'<circle class="live" cx="{lx + 4}" cy="{P + 10}" r="4" fill="{th["green"]}"/>',
                  t(lx + 13, P + 14, "LIVE", 10.5, th["green"], bold=True)]
    y = P + 46
    title_lines = wrap(title, 18, inner, bold=True)
    if len(title_lines) > 2:
        raise ValueError(f"title too long for a card: {title}")
    for line in title_lines:
        parts.append(t(P, y, line, 18, th["ink"], bold=True))
        y += 23
    y += 2
    blurb_lines = wrap(blurb, 12.5, inner)
    if len(title_lines) + len(blurb_lines) > 4:  # more would run into the numbers
        raise ValueError(f"blurb too long for a card: {blurb}")
    for line in blurb_lines:
        parts.append(t(P, y, line, 12.5, th["muted"]))
        y += 17
    # two numbers side by side, anchored to the bottom so every card lines up
    top = H - P - 128
    parts.append(f'<line x1="{P}" x2="{W - P}" y1="{top - 10}" y2="{top - 10}" stroke="{th["border"]}"/>')
    col = inner / 2
    for i, (value, label) in enumerate(stats):
        x = P + col * i + (10 if i else 0)
        parts.append(t(x, top + 22, value, 25, th["blue" if i == 0 else "orange"], bold=True))
        lines = fit(label, 11.5, col - 14, max_lines=2)
        for j, line in enumerate(lines):
            parts.append(t(x, top + 42 + 15 * j, line, 11.5, th["muted"]))
    cx = P
    for c in chips:
        s, w = chip(cx, H - P - 42, c, th, size=11, h=22)
        if cx + w > W - P:
            break  # drop tools that don't fit rather than overflow the card
        parts.append(s)
        cx += w + 6
    parts.append(t(P, H - P + 1, fit(footer, 11, inner, max_lines=1)[0], 11, th["muted"]))
    return svg(W, H, "".join(parts), f"{title}: {stats[0][0]} {stats[0][1]}; {stats[1][0]} {stats[1][1]}")


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



# --------------------------------------------------------------------------- README sections
def grid_markdown() -> str:
    """Two cards per row; each card opens the project's live site."""
    rows = []
    for i in range(0, len(PROJECTS), 2):
        cells = [f'<a href="{site(p["repo"])}"><img src="assets/card-{p["key"]}.svg" width="49%" '
                 f'alt="{escape(p["card"]["title"])} project card"></a>' for p in PROJECTS[i:i + 2]]
        rows.append('<p align="center">\n' + "\n".join(cells) + "\n</p>")
    return "\n".join(rows)


def table_markdown() -> str:
    head = "| Project | Best fit for | Links |\n|---|---|---|"
    lines = [f"| **{p['card']['title']}** | {', '.join(r for r in ROLES if r in p['roles'])} | "
             f"[{p['card']['link']}]({site(p['repo'])}) · [Code](https://github.com/{USER}/{p['repo']}) |"
             for p in PROJECTS]
    return head + "\n" + "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    pattern = rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)"
    if not re.search(pattern, text, flags=re.S):
        raise ValueError(f"README is missing the {name} markers")
    return re.sub(pattern, lambda m: f"{m.group(1)}\n{body}\n{m.group(2)}", text, flags=re.S)


# --------------------------------------------------------------------------- build
def build(stats: dict) -> None:
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "banner.svg").write_text(banner())
    (ASSETS / "badge-linkedin.svg").write_text(badge("LinkedIn", "davidskaff1", "#0a66c2"))
    (ASSETS / "badge-available.svg").write_text(badge("Available", "January 2027", "#1a7f37"))
    (ASSETS / "badge-location.svg").write_text(badge("Work", "U.S. (TN-eligible)", "#d9541f"))

    th = THEMES[THEME]
    keep = {"banner.svg", "badge-linkedin.svg", "badge-available.svg", "badge-location.svg", "toolkit.svg"}
    for p in PROJECTS:
        if p["key"] not in stats:
            raise KeyError(f"no numbers for {p['key']} (not fetched and not in data/latest.json)")
        numbers, footer = card_numbers(p["key"], stats[p["key"]])
        c = p["card"]
        name = f"card-{p['key']}.svg"
        (ASSETS / name).write_text(project_card(th, tag=c["tag"], color=c["color"], title=c["title"],
                                                blurb=c["blurb"], stats=numbers, chips=c["chips"], footer=footer))
        keep.add(name)
    (ASSETS / "toolkit.svg").write_text(toolkit(th))
    for old in ASSETS.glob("*.svg"):  # cards of projects no longer listed
        if old.name not in keep:
            old.unlink()

    stamp = date.today().strftime("%b %-d, %Y")
    text = README.read_text()
    text = replace_block(text, "PROJECTS", grid_markdown())
    text = replace_block(text, "ROLES", table_markdown())
    text = re.sub(r"(<!-- REFRESHED -->).*?(<!-- /REFRESHED -->)", lambda m: m.group(1) + stamp + m.group(2), text)
    README.write_text(text)
    print(f"built {len(list(ASSETS.glob('*.svg')))} graphics, stamped {stamp} at {datetime.now(UTC):%H:%M} UTC")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip fetching; rebuild from data/latest.json")
    build(load(parser.parse_args().offline))
