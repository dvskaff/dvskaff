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
import csv
import io
import json
import re
import sys
import urllib.request
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from font_metrics import BOLD, CHARS, REGULAR  # noqa: E402
from manhattan import MANHATTAN, MAP_H, MAP_W, TOLL_ZONE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS, CACHE, README = ROOT / "assets", ROOT / "data" / "latest.json", ROOT / "README.md"
USER = "dvskaff"
FONT = "Helvetica, Arial, sans-serif"

# Every graphic uses this palette, so the profile looks dark whatever theme the visitor's GitHub uses.
THEME = "navy"
THEMES = {
    "navy": dict(bg="#0a1424", card="#0f1c30", border="#24374f", ink="#eef4fb", muted="#9fb3cb",
                 chip="#0a1424", chip_border="#2c4363", blue="#5aa8ff", orange="#f2804f", green="#3fcf6e",
                 violet="#a493ff", base="#4b6283"),
    "light": dict(bg="#ffffff", card="#f6f8fa", border="#d0d7de", ink="#1f2328", muted="#59636e",
                  chip="#ffffff", chip_border="#d0d7de", blue="#1f6fd1", orange="#d9541f", green="#1a7f37",
                  violet="#5a45c9", base="#afb8c1"),
    "dark": dict(bg="#0d1117", card="#151b23", border="#30363d", ink="#e6edf3", muted="#9198a1",
                 chip="#0d1117", chip_border="#3d444d", blue="#4c9df5", orange="#f2804f", green="#3fb950",
                 violet="#9d8cf2", base="#545d68"),
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
def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"{USER}-profile-refresh"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URLs)
        return r.read().decode("utf-8")


def fetch(url: str) -> dict:
    return json.loads(fetch_text(url))


def fetch_csv(url: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(fetch_text(url))))


def citibike_stats(m: dict) -> dict:
    summary = {row["model"]: row for row in m["summary"]}
    champ = m["champion"]
    baselines = [summary[n]["WAPE"] for n in ("Seasonal naive", "AutoETS", "AutoARIMA") if n in summary]
    out = {"wape": summary[champ]["WAPE"], "error_cut": 1 - summary[champ]["WAPE"] / min(baselines),
           "rides": m["total_rides"], "data_end": m.get("data_end")}  # fmt: skip
    try:  # the published 30-day forecast, drawn on the card
        rows = [r for r in fetch_csv(site("citibike-demand-forecasting") + "data/citibike_forecast.csv")
                if r["date"] > str(m.get("data_end"))[:10]]  # fmt: skip
        out["forecast"] = {"dates": [r["date"] for r in rows], "yhat": [float(r["yhat"]) for r in rows],
                           "lo": [float(r["lo_80"]) for r in rows], "hi": [float(r["hi_80"]) for r in rows]}  # fmt: skip
    except Exception as exc:
        print(f"WARNING: no Citi Bike forecast file ({exc!r})")
    return out


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
            "models": d["models"], "within_7d": r["pct_closed_within_7d"],
            "data_end": str(r["data_as_of"])[:10]}  # fmt: skip


def community_stats(r: dict) -> dict:
    fast = next(e for e in r["experience"] if e["signal"] == "first_q_answered_fast")
    return {"came_back": r["newcomer_came_back_rate"], "fast_answer_pts": fast["diff"],
            "fast_yes": fast["rate_yes"], "fast_no": fast["rate_no"],
            "contributions": r["contributions_all_time"], "data_end": str(r["data_end"])[:10]}  # fmt: skip


def mortgage_stats(r: dict) -> dict:
    main = next(m for m in r["metrics"] if m["model"] == "LightGBM, monotonic + calibrated")
    h = r["headline"]
    return {"auc": main["roc_auc"], "applications": r["n_applications"], "group": h["group"],
            "odds_raw": h["odds_ratio_unadjusted"], "odds_adj": h["odds_ratio_adjusted"]}  # fmt: skip


def flights_stats(r: dict) -> dict:
    return {"flights": r["lake"]["silver/flights"]["rows"], "late_after_60": r["late_after_60"],
            "on_time": r["last12"]["on_time_rate"], "data_end": r["window"][1],
            "gold_tables": sum(k.startswith("gold/") for k in r["lake"])}  # fmt: skip


def ontario_stats(r: dict) -> dict:
    bt = r["backtest"]
    out = {"mape": bt["model"]["mape"], "naive_mape": bt["naive_week"]["mape"],
           "error_cut": bt["improvement_vs_naive"], "data_end": r["data_end"]}  # fmt: skip
    try:  # tomorrow's hourly forecast (the data behind the what-if page), drawn on the card
        w = fetch(site("ontario-demand-forecasting") + "data/whatif.json")
        out["forecast"] = {"day": w["target_day"], "hour_ending": w["hour_ending"],
                           **{k: w["forecast"][k] for k in ("p10", "p50", "p90")}}  # fmt: skip
    except Exception as exc:
        print(f"WARNING: no Ontario hourly forecast ({exc!r})")
    return out


def filings_stats(r: dict) -> dict:
    g = r["generation"]
    return {"recall5": r["retrieval_best"]["recall@5"], "bm25_recall5": r["retrieval_bm25"]["recall@5"],
            "correct_rag": g["RAG (single filing)"]["correct"],
            "correct_closed": g["Closed book (no retrieval)"]["correct"], "documents": r["documents"]}  # fmt: skip


def complaints_stats(r: dict) -> dict:
    best = r["scores"]["Ensemble: fine-tuned + TF-IDF"]
    return {"accuracy": best["accuracy"], "macro_f1": best["macro_f1"], "auto_share": best["routing"]["auto_share"],
            "auto_accuracy": best["routing"]["auto_accuracy"], "test": r["run"]["test"],
            "products": len(r["run"]["labels"])}  # fmt: skip


def congestion_stats(r: dict) -> dict:
    sp, bus = r["speed"], r.get("bus") or {}
    return {"first": sp["first"]["effect"], "first_year": sp["first"]["year"], "trend": sp["trend"]["effect"],
            "bus_first": (bus.get("first") or {}).get("effect"), "rides": r["rides"],
            "data_end": r["data_through"]}  # fmt: skip


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
    P("complaints", "complaint-routing-nlp", "results.json", complaints_stats, ["Data Scientist", "ML Engineer"],
      tag="NLP · TRANSFORMERS", color="violet", title="Consumer Complaint Routing with Transformers",
      blurb="Which team should handle each consumer complaint, and when should a person step in? A fine-tuned, "
            "calibrated transformer.",
      chips=["PyTorch", "Transformers", "ONNX", "BERTopic", "scikit-learn"], link="Live dashboard & demo"),
    P("mortgage", "mortgage-fair-lending-model", "results.json", mortgage_stats, ["Data Scientist"],
      tag="CREDIT RISK · FAIRNESS", color="orange", title="Mortgage Denial Modeling & Fair-Lending Audit",
      blurb="Which mortgage applications get denied, and are some groups denied more after accounting for "
            "credit factors? Public HMDA data for Georgia.",
      chips=["LightGBM", "Scorecard", "Fairlearn", "SHAP", "statsmodels"], link="Live dashboard"),
    P("congestion", "congestion-pricing-impact", "results.json", congestion_stats, ["Data Analyst", "Data Scientist"],
      tag="CAUSAL INFERENCE", color="orange", title="NYC Congestion Pricing Impact",
      blurb="Did New York's Manhattan toll make traffic faster? Difference-in-differences on a billion taxi and "
            "ride-hail trips, checked against MTA bus speeds.",
      chips=["DuckDB", "statsmodels", "Plotly", "SQL", "Parquet"], link="Live dashboard & map"),
    P("email", "email-campaign-uplift", "results.json", email_stats, ["Data Analyst", "Data Scientist"],
      tag="A/B TESTING · UPLIFT", color="orange", title="Email Campaign Experiment & Uplift Modeling",
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
            fresh = p["stats_fn"](raw)
            # an optional extra file (a forecast to draw) that couldn't be read keeps its last good version
            out[p["key"]] = {**{k: v for k, v in cached.get(p["key"], {}).items() if k not in fresh}, **fresh}
            print(f"fetched {p['key']}: {out[p['key']]}")
        except Exception as exc:  # keep the last good numbers if a site is briefly unavailable or changed
            print(f"WARNING: could not refresh {p['key']} ({exc!r}); keeping cached values")
    CACHE.write_text(json.dumps(out, indent=2) + "\n")
    return out


def card_content(key: str, s: dict) -> dict:
    """What each card shows below its description: one headline number, one small visual that fits the project
    (a forecast curve, a comparison, a pipeline, a map...), and a footer saying how it stays up to date.

    Visuals: ("bars", caption, [(label, value, value_text, highlight)]), ("line", caption, series),
    ("split", caption, share, left_lines, right_lines), ("odds", caption, raw, adjusted),
    ("pipeline", caption, [(name, detail)]), ("map", caption). Every number comes from the live results."""
    def bars(caption, rows):
        return ("bars", caption, rows)

    if key == "ontario":
        fc = s.get("forecast")
        if fc:
            visual = ("line", f"Forecast for {fmt_date(fc['day'])[:-6]}, hour by hour",
                      {"y": fc["p50"], "lo": fc["p10"], "hi": fc["p90"], "start": "1 a.m.", "end": "midnight",
                       "peak": lambda i, v: f"peak {v / 1000:.1f} GW"})  # fmt: skip
        else:
            visual = bars("Forecast error (MAPE)", [("LightGBM model", s["mape"], f"{s['mape']:.1f}%", True),
                                                    ("Same hour last week", s["naive_mape"], f"{s['naive_mape']:.1f}%",
                                                     False)])  # fmt: skip
        return {"headline": (f"{s['mape']:.1f}%", f"forecast error (MAPE), {s['error_cut']:.0%} below the same hour "
                                                  f"last week ({s['naive_mape']:.1f}%)"),
                "visual": visual,
                "footer": f"Forecasts daily, retrains weekly · data through {fmt_date(s['data_end'])}"}
    if key == "filings":
        return {"headline": (f"{s['recall5']:.0%}", f"of questions find the evidence page in the top 5 (BM25: "
                                                    f"{s['bm25_recall5']:.0%})"),
                "visual": bars("Answered correctly", [("With retrieval (RAG)", s["correct_rag"],
                                                       f"{s['correct_rag']:.0%}", True),
                                                      ("Without retrieval", s["correct_closed"],
                                                       f"{s['correct_closed']:.0%}", False)]),
                "footer": f"{s['documents']} SEC filings · re-evaluated on every code change"}
    if key == "complaints":
        return {"headline": (f"{s['accuracy']:.0%}", f"routed to the right team across {s['products']} products "
                                                     f"(macro-F1 {s['macro_f1']:.2f})"),
                "visual": ("split", "Routing policy", s["auto_share"],
                           [f"{s['auto_share']:.0%} auto-routed", f"{s['auto_accuracy']:.0%} of them correct"],
                           [f"{1 - s['auto_share']:.0%} to a person", "with two suggestions"]),
                "footer": f"Tested on {s['test'] / 1000:.0f}K recent complaints · quality-gated on every run"}
    if key == "mortgage":
        return {"headline": (f"{s['auc']:.2f}", f"ROC AUC on a held-out year ({s['applications'] / 1000:.0f}K "
                                                "applications)"),
                "visual": ("odds", f"{s['group']} vs. White denial odds", s["odds_raw"], s["odds_adj"]),
                "footer": "Checks for new HMDA data monthly"}
    if key == "congestion":
        return {"headline": (f"{s['first']:+.1%}", f"faster traffic inside the toll zone in its first year, "
                                                   f"{s['first_year']} ({s['trend']:+.0%} trend-adjusted)"),
                "visual": ("map", "Manhattan · toll zone in blue"),
                "footer": f"{s['rides'] / 1e9:.1f}B trips · updated weekly · data through {month(s['data_end'])}"}
    if key == "email":
        return {"headline": (f"+{s['visit_lift']:.0%}", f"site visits from the best email ({s['customers'] / 1000:.0f}K "
                                                         "customers)"),
                "visual": bars("Estimated campaign profit", [
                    ("Email everyone", 1.0, "baseline", False),
                    ("Target with the uplift model", 1 + s["profit_gain"], f"+{s['profit_gain']:.0%}", True)]),
                "footer": "Analysis reruns on every push"}
    if key == "flights":
        return {"headline": (f"{s['late_after_60']:.0%}", "of next flights leave late after a 60+ min late arrival"),
                "visual": ("pipeline", "Delta Lake medallion layers",
                           [("Bronze", "raw files"), ("Silver", f"{s['flights'] / 1e6:.1f}M rows"),
                            ("Gold", f"{s.get('gold_tables', 10)} tables")]),
                "footer": f"Airflow loads new months weekly · data through {month(s['data_end'])}"}
    if key == "chicago311":
        return {"headline": (f"{s['median_days']:.1f} days", "median time to close a service request"
                             + (f" ({s['within_7d']:.0%} within a week)" if s.get("within_7d") else "")),
                "visual": ("pipeline", "dbt warehouse, loaded daily",
                           [("Raw", f"{s['warehouse'] / 1e6:.1f}M rows"), ("Models", f"{s.get('models', 16)} in dbt"),
                            ("Tests", f"{s['tests']} checks")]),
                "footer": f"{s['requests_12m'] / 1000:.0f}K requests in the last year · data through "
                          f"{fmt_date(s['data_end'])}"}
    if key == "community":
        if s.get("fast_yes") is not None:
            visual = bars("Came back within 30 days", [
                ("First question answered within 24 h", s["fast_yes"], f"{s['fast_yes']:.0%}", True),
                ("Not answered within 24 h", s["fast_no"], f"{s['fast_no']:.0%}", False)])
        else:
            visual = bars("Came back within 30 days", [("All newcomers", s["came_back"], f"{s['came_back']:.0%}", True)])
        return {"headline": (f"{s['came_back']:.0%}", "of newcomers come back within 30 days"),
                "visual": visual,
                "footer": f"{s['contributions'] / 1e6:.1f}M contributions · data through {month(s['data_end'])}"}
    if key == "citibike":
        fc = s.get("forecast")
        if fc and fc.get("yhat"):
            d0, d1 = (datetime.strptime(fc["dates"][i], "%Y-%m-%d") for i in (0, -1))
            visual = ("line", "Next 30 days, rides per day",
                      {"y": fc["yhat"], "lo": fc["lo"], "hi": fc["hi"], "start": f"{d0:%b %-d}", "end": f"{d1:%b %-d}",
                       "peak": None})  # fmt: skip
        else:
            base = s["wape"] / (1 - s["error_cut"])
            visual = bars("Forecast error (WAPE)", [("LightGBM model", s["wape"], f"{s['wape']:.1%}", True),
                                                    ("Best statistical baseline", base, f"{base:.1%}", False)])
        return {"headline": (f"{s['wape']:.1%}", f"forecast error (WAPE) 30 days ahead, {s['error_cut']:.0%} below "
                                                 "the best statistical baseline"),
                "visual": visual,
                "footer": f"Retrains monthly · data through {fmt_date(s['data_end'])}"}
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


def caption(th, x, y, text, w):
    """Small uppercase label above a visual; shortened with an ellipsis if it would run past the card."""
    text = text.upper()

    def width(s):
        return text_width(s, 9.5, bold=True) + 0.4 * len(s)

    if width(text) > w:
        while text and width(text + "…") > w:
            text = text[:-1]
        text = text.rstrip(" ,·") + "…"
    return t(x, y, text, 9.5, th["muted"], bold=True, extra=' letter-spacing="0.4"')


def v_bars(th, x, y, w, cap, rows):
    """Horizontal bars from zero: the project's result in the accent blue, the comparison in a neutral tone."""
    parts = [caption(th, x, y + 9, cap, w)]
    vmax = max(r[1] for r in rows) or 1
    span = w - 46
    for i, (label, value, text, hi) in enumerate(rows):
        ly = y + 27 + i * 25
        parts.append(t(x, ly, fit(label, 10.5, w, 1)[0], 10.5, th["muted"]))
        bw = max(3.0, value / vmax * span)
        parts.append(f'<rect x="{x}" y="{ly + 5}" width="{bw:.1f}" height="8" rx="2" '
                     f'fill="{th["blue"] if hi else th["base"]}"/>')
        parts.append(t(x + bw + 6, ly + 12.5, text, 11, th["ink"], bold=True))
    return "".join(parts)


def v_line(th, x, y, w, cap, d):
    """A forecast: the central line with its uncertainty band, start and end labels, optional peak marker."""
    parts = [caption(th, x, y + 9, cap, w)]
    ys, lo, hi = d["y"], d.get("lo") or d["y"], d.get("hi") or d["y"]
    top, h = y + 22, 36
    vmin, vmax = min(lo), max(hi)
    rng = (vmax - vmin) or 1
    n = len(ys)

    def px(i):
        return x + i * w / (n - 1)

    def py(v):
        return top + h - (v - vmin) / rng * h

    band = [f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(hi)] + \
           [f"{px(i):.1f},{py(v):.1f}" for i, v in reversed(list(enumerate(lo)))]
    parts.append(f'<polygon points="{" ".join(band)}" fill="{th["blue"]}" fill-opacity="0.18"/>')
    line = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(ys))
    parts.append(f'<polyline points="{line}" fill="none" stroke="{th["blue"]}" stroke-width="2" '
                 'stroke-linejoin="round" stroke-linecap="round"/>')
    parts.append(f'<line x1="{x}" x2="{x + w}" y1="{top + h + 3}" y2="{top + h + 3}" stroke="{th["border"]}"/>')
    parts.append(t(x, top + h + 16, d["start"], 10, th["muted"]))
    parts.append(t(x + w, top + h + 16, d["end"], 10, th["muted"], anchor="end"))
    if d.get("peak"):
        i = max(range(n), key=lambda k: ys[k])
        parts.append(f'<circle cx="{px(i):.1f}" cy="{py(ys[i]):.1f}" r="3.5" fill="{th["blue"]}" '
                     f'stroke="{th["card"]}" stroke-width="2"/>')
        parts.append(t(x + w / 2, top + h + 16, d["peak"](i, ys[i]), 10, th["ink"], anchor="middle"))
    return "".join(parts)


def v_split(th, x, y, w, cap, share, left, right):
    """One 100% bar split in two, with the two parts labeled underneath."""
    parts = [caption(th, x, y + 9, cap, w)]
    lw = max(4.0, w * share - 1)
    parts.append(f'<rect x="{x}" y="{y + 19}" width="{lw:.1f}" height="12" rx="2" fill="{th["blue"]}"/>')
    parts.append(f'<rect x="{x + lw + 2:.1f}" y="{y + 19}" width="{max(0.0, w - lw - 2):.1f}" height="12" rx="2" '
                 f'fill="{th["base"]}"/>')
    for j, line in enumerate(left):
        parts.append(t(x, y + 47 + 14 * j, line, 11 if j == 0 else 10.5, th["ink" if j == 0 else "muted"], bold=j == 0))
    for j, line in enumerate(right):
        parts.append(t(x + w, y + 47 + 14 * j, line, 11 if j == 0 else 10.5, th["ink" if j == 0 else "muted"],
                       bold=j == 0, anchor="end"))
    return "".join(parts)


def v_odds(th, x, y, w, cap, raw, adj):
    """Odds ratio before and after controls, on a scale starting at 1x (equal odds)."""
    parts = [caption(th, x, y + 9, cap, w)]
    lo, hi = 1.0, max(raw, adj, 1.5) * 1.08
    ax = y + 42

    def px(v):
        return x + 6 + (v - lo) / (hi - lo) * (w - 12)

    parts.append(f'<line x1="{x}" x2="{x + w}" y1="{ax}" y2="{ax}" stroke="{th["border"]}"/>')
    parts.append(f'<line x1="{px(1):.1f}" x2="{px(1):.1f}" y1="{ax - 6}" y2="{ax + 6}" stroke="{th["muted"]}"/>')
    parts.append(t(px(1), ax - 11, "1× = equal", 10, th["muted"]))
    parts.append(f'<line x1="{px(adj):.1f}" x2="{px(raw):.1f}" y1="{ax}" y2="{ax}" stroke="{th["base"]}" '
                 'stroke-width="3"/>')
    for v, color in ((raw, th["base"]), (adj, th["blue"])):
        parts.append(f'<circle cx="{px(v):.1f}" cy="{ax}" r="5.5" fill="{color}" stroke="{th["card"]}" '
                     'stroke-width="2"/>')

    def centered(cx, text, size, bold=False):  # centered on the dot, kept inside the visual
        half = text_width(text, size, bold) / 2
        return min(max(cx, x + half), x + w - half)

    raw_txt, adj_txt = f"{raw:.1f}× unadjusted", f"{adj:.1f}× after credit factors"
    parts.append(t(centered(px(raw), raw_txt, 10.5), ax - 11, raw_txt, 10.5, th["muted"], anchor="middle"))
    parts.append(t(centered(px(adj), adj_txt, 10.5, True), ax + 21, adj_txt, 10.5, th["ink"], bold=True,
                   anchor="middle"))
    return "".join(parts)


def v_pipeline(th, x, y, w, cap, stages):
    """Boxes and arrows: the layers data moves through, with live sizes."""
    parts = [caption(th, x, y + 9, cap, w)]
    gap = 12
    bw = (w - gap * (len(stages) - 1)) / len(stages)
    for i, (name, detail) in enumerate(stages):
        bx = x + i * (bw + gap)
        last = i == len(stages) - 1
        parts.append(f'<rect x="{bx:.1f}" y="{y + 18}" width="{bw:.1f}" height="42" rx="7" fill="{th["chip"]}" '
                     f'stroke="{th["blue"] if last else th["chip_border"]}"/>')
        parts.append(t(bx + bw / 2, y + 35, name, 11, th["ink"], bold=True, anchor="middle"))
        parts.append(t(bx + bw / 2, y + 50, fit(detail, 10, bw - 6, 1)[0], 10, th["muted"], anchor="middle"))
        if not last:
            ax = bx + bw + 2
            parts.append(f'<path d="M{ax:.1f} {y + 39}h{gap - 6}" stroke="{th["muted"]}" stroke-width="1.5"/>'
                         f'<path d="M{ax + gap - 7:.1f} {y + 35.5}l4 3.5-4 3.5z" fill="{th["muted"]}"/>')
    return "".join(parts)


def v_map(th, x, y, w, cap):
    """Manhattan (north to the right) with the toll zone highlighted."""
    k = w / MAP_W
    parts = [caption(th, x, y + 9, cap, w),
             f'<g transform="translate({x} {y + 17}) scale({k:.4f})">'
             f'<path d="{MANHATTAN}" fill="{th["base"]}"/><path d="{TOLL_ZONE}" fill="{th["blue"]}"/></g>',
             t(x, y + 17 + MAP_H * k + 13, "south of 60th St", 10, th["muted"]),
             t(x + w, y + 17 + MAP_H * k + 13, "north →", 10, th["muted"], anchor="end")]
    return "".join(parts)


VISUALS = {"bars": v_bars, "line": v_line, "split": v_split, "odds": v_odds, "pipeline": v_pipeline, "map": v_map}


def project_card(th: dict, *, tag, color, title, blurb, headline, visual, chips, footer, live=True) -> str:
    """Half-width card (two per row): tag, title, the business question, one live number with a small visual that
    fits the project, tools, freshness. Every card has the same frame, so the grid lines up."""
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
    # bottom block, anchored to the bottom so every card lines up: headline number left, visual right
    top = H - P - 128
    parts.append(f'<line x1="{P}" x2="{W - P}" y1="{top - 10}" y2="{top - 10}" stroke="{th["border"]}"/>')
    left_w = 168
    value, label = headline
    parts.append(t(P, top + 22, value, 27, th["ink"], bold=True))
    for j, line in enumerate(fit(label, 11.5, left_w - 8, max_lines=3)):
        parts.append(t(P, top + 42 + 15 * j, line, 11.5, th["muted"]))
    vx = P + left_w + 14
    kind, cap, *args = visual
    parts.append(VISUALS[kind](th, vx, top - 2, W - P - vx, cap, *args))
    cx = P
    for c in chips:
        s, w = chip(cx, H - P - 42, c, th, size=11, h=22)
        if cx + w > W - P:
            break  # drop tools that don't fit rather than overflow the card
        parts.append(s)
        cx += w + 6
    parts.append(t(P, H - P + 1, fit(footer, 11, inner, max_lines=1)[0], 11, th["muted"]))
    return svg(W, H, "".join(parts), f"{title}: {value} {label}")


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
        content = card_content(p["key"], stats[p["key"]])
        c = p["card"]
        name = f"card-{p['key']}.svg"
        (ASSETS / name).write_text(project_card(th, tag=c["tag"], color=c["color"], title=c["title"],
                                                blurb=c["blurb"], chips=c["chips"], **content))
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
