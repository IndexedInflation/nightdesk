"""The lab: try changes to the forecaster and grade each one on the fair list.

Rules of the lab (so nothing here can flatter itself):
  - Every variant is trained year by year and graded only on years it had not seen.
  - Grading uses 2010 to 2024 only. The 2025-26 exam months are never looked at here.
  - A company counts only on days it was really in the S&P 500.
  - One model per horizon (quick). A winner is re-run properly with three before it goes live.

    .venv/bin/python -m nightdesk.lab base events        (run the named variants)
    .venv/bin/python -m nightdesk.lab --list
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from . import backtest as B
from . import events as EV
from . import features as F
from . import pit, research, universe
from .config import EVENT_FAMILIES, RESEARCH, SCORE_SMOOTHING, TARGET
from .model import ModelSpec
from .portfolio import smooth_scores

LAB = RESEARCH / "lab"
END = "2024-12-31"
THREADS = int(os.environ.get("LAB_THREADS", "5"))

# name -> what changes from the base recipe.  "use" = which families of signals the model may see.
VARIANTS: dict[str, dict] = {
    "base": {"label": "The model as it is (price signals only)", "use": ["price"]},
    "earn": {"label": "Add earnings signals", "use": ["price", "earn"]},
    "analyst": {"label": "Add analyst signals", "use": ["price", "analyst"]},
    "events": {"label": "Add earnings and analyst signals", "use": ["price", "earn", "analyst"]},
    # Round two: analysts did not help, earnings did. Everything below builds on price + earnings.
    "earn_month": {"label": "Earnings, forecasting a month and a quarter ahead (to match how long it holds)", "use": ["price", "earn"], "spec": {"horizons": (21, 63)}},
    "earn_h63": {"label": "Earnings, also forecasting a quarter ahead", "use": ["price", "earn"], "spec": {"horizons": (5, 21, 63)}},
    "earn_risk": {"label": "Earnings, answers adjusted for how jumpy each stock is", "use": ["price", "earn"], "targets": "risk"},
    "earn_sector": {"label": "Earnings, answers measured against each stock's own industry", "use": ["price", "earn"], "targets": "sector"},
    "earn_slow": {"label": "Earnings, more trees learning slower", "use": ["price", "earn"], "spec": {"n_trees": 600, "learning_rate": 0.025}},
    "earn_simple": {"label": "Earnings, simpler trees (harder to fool)", "use": ["price", "earn"], "spec": {"num_leaves": 15, "min_leaf": 5000}},
    "earn_wide": {"label": "Earnings, trained on every stock-day (not only days in the index)", "use": ["price", "earn"], "wide": True},
    "earn_line": {"label": "Earnings, more weight on the straight-line models", "use": ["price", "earn"], "spec": {"ridge_weight": 0.6}},
    "earn_ext": {"label": "Earnings plus nine more price signals", "use": ["price", "earn", "ext"]},
    "earn_recent": {"label": "Earnings, recent years count more", "use": ["price", "earn"], "spec": {"decay_years": 6.0}},
    # Round three: the risk-adjusted answers were the big step. Which parts of it matter?
    "risk": {"label": "Price signals only, answers adjusted for how jumpy each stock is", "use": ["price"], "targets": "risk"},
    "risk_ext": {"label": "Risk-adjusted answers, plus nine more price signals", "use": ["price", "ext"], "targets": "risk"},
    "earn_risk_s": {"label": "Earnings + risk-adjusted answers, simpler trees", "use": ["price", "earn"], "targets": "risk", "spec": {"num_leaves": 15, "min_leaf": 5000}},
    "earn_risk_w": {"label": "Earnings + risk-adjusted answers, trained on every stock-day", "use": ["price", "earn"], "targets": "risk", "wide": True},
    "earn_risk_b": {"label": "Earnings + risk-adjusted answers, a second roll of the dice (different random seed)", "use": ["price", "earn"], "targets": "risk", "spec": {"seeds": (22,)}},
    "risk_b": {"label": "Price signals + risk-adjusted answers, a second roll of the dice", "use": ["price"], "targets": "risk", "spec": {"seeds": (22,)}},
    # Round four: measuring against the stock's own industry sharpened the ranking; adjusting for jumpiness cut the risk. Both at once?
    "earn_both": {"label": "Earnings, answers measured against the industry AND adjusted for jumpiness", "use": ["price", "earn"], "targets": "both"},
    "both": {"label": "Price signals only, answers measured against the industry AND adjusted for jumpiness", "use": ["price"], "targets": "both"},
    "sector": {"label": "Price signals only, answers measured against each stock's own industry", "use": ["price"], "targets": "sector"},
    "both_b": {"label": "Price signals, both answer changes, a second roll of the dice", "use": ["price"], "targets": "both", "spec": {"seeds": (22,)}},
    "earn_both_b": {"label": "Earnings, both answer changes, a second roll of the dice", "use": ["price", "earn"], "targets": "both", "spec": {"seeds": (22,)}},
}


def family(name: str, base: set[str]) -> str:
    if name.startswith(("earn_", "eps_")):
        return "earn"
    if name.startswith("an_"):
        return "analyst"
    return "price" if name in base else "ext"


def build(wide: bool = False, log=print):
    """One big table holding every candidate signal; each variant picks the columns it is allowed."""
    panel, sectors, member = pit.pit_panel(lambda m: None)
    industries = {r["symbol"]: r["industry"] for r in universe.load_universe()}
    ev = EV.load()
    t0 = time.time()
    ds = F.build_dataset(panel, sectors, horizons=(1, 5, 21, 63), risk_adjusted=True, industries=industries,
                         membership=None if wide else member, events=ev)
    base = set(F.compute_features({f: df.iloc[-300:] for f, df in panel.items()}, sectors))
    fams = [family(n, base) for n in ds.feature_names]
    log(f"table ready in {time.time() - t0:.0f}s: {len(ds.dates):,} rows x {len(ds.feature_names)} signals "
        f"({', '.join(f'{k} {fams.count(k)}' for k in ('price', 'ext', 'earn', 'analyst'))}){' [every stock-day]' if wide else ''}")
    return panel, sectors, member, ds, fams


def view(ds: F.Dataset, fams: list[str], use: list[str]) -> F.Dataset:
    cols = [j for j, fam in enumerate(fams) if fam in use]
    out = F.Dataset(X=np.ascontiguousarray(ds.X[:, cols]), y=ds.y, fwd=ds.fwd, dates=ds.dates, symbols=ds.symbols,
                    feature_names=[ds.feature_names[j] for j in cols])
    out.y_risk, out.y_sector, out.y_both = ds.y_risk, ds.y_sector, ds.y_both
    return out


def grade(scores: pd.DataFrame, panel, sectors, fwd: dict) -> dict:
    """How good is this set of scores, on the fair list, 2010 to 2024?"""
    spy = panel["adj"]["SPY"].pct_change(fill_method=None)
    ic = {h: B.information_coefficient(scores, fwd[h]) for h in (5, 21)}
    by_year = ic[5]["series"].groupby(ic[5]["series"].index.year).mean()
    blended = smooth_scores(scores, SCORE_SMOOTHING)
    acc = B.pick_accuracy(blended, fwd[5], top=50)
    acc_m = B.pick_accuracy(blended, fwd[21], top=50)
    buckets = B.bucket_returns(scores, fwd[5])
    out = {"ic_week": ic[5]["mean"], "ic_week_t": ic[5]["t_stat"], "ic_month": ic[21]["mean"], "ic_month_t": ic[21]["t_stat"],
           "ic_week_se": ic[5]["std"] / len(scores) ** 0.5, "ic_years_positive": int((by_year > 0).sum()), "ic_years": int(len(by_year)),
           "ic_by_year": {int(y): float(v) for y, v in by_year.items()},
           "pick_beats_middle_week": acc["picks_beat_average"], "pick_beats_middle_month": acc_m["picks_beat_average"],
           "group_beats_average_week": acc["group_beats_average"], "spread_week_bps": (buckets[-1] - buckets[0]) * 1e4}
    for key in ("cautious", "steady", "full"):
        prm = pit.recipe(key)
        res = B.simulate(blended, panel, prm, sectors)
        m = B.metrics(res.returns, spy.reindex(res.returns.index).fillna(0.0))
        out[key] = {k: m[k] for k in ("cagr", "sharpe", "max_drawdown", "vol", "beta", "alpha")}
        out[key]["turnover"] = float(res.turnover.sum() / (len(scores) / 252))
    return out


def run(names: list[str], log=print) -> None:
    LAB.mkdir(exist_ok=True)
    tables: dict[bool, tuple] = {}
    for name in names:
        v = VARIANTS[name]
        wide = bool(v.get("wide"))
        if wide not in tables:
            tables[wide] = build(wide, log)
        panel, sectors, member, ds, fams = tables[wide]
        fwd = {h: F.forward_returns(panel, h) for h in (5, 21)}
        t0 = time.time()
        sub = view(ds, fams, v["use"])
        spec = replace(ModelSpec(seeds=(11,), threads=THREADS), **v.get("spec", {}))
        targets = {"sector": sub.y_sector, "risk": sub.y_risk, "both": sub.y_both}.get(v.get("targets"))
        scores = research.walk_forward(sub, spec, 2010, END, log=lambda m: None, targets=targets)
        if wide:                                              # trained on everything, but graded only on real index days
            scores = scores.where(member.reindex(index=scores.index, columns=scores.columns, fill_value=False)).dropna(how="all")
        row = {"name": name, "label": v["label"], "signals": len(sub.feature_names), **grade(scores, panel, sectors, fwd),
               "minutes": (time.time() - t0) / 60, "at": time.time()}
        scores.to_parquet(LAB / f"scores_{name}.parquet")
        (LAB / f"{name}.json").write_text(json.dumps(research._clean(row)))
        c, f = row["cautious"], row["full"]
        log(f"{name:<14} skill week {row['ic_week']:.4f} (t {row['ic_week_t']:.1f}) month {row['ic_month']:.4f} | good years {row['ic_years_positive']}/{row['ic_years']} | "
            f"picks beat middle {row['pick_beats_middle_week']:.1%} wk {row['pick_beats_middle_month']:.1%} mo | "
            f"cautious {c['cagr']:+.1%} Sh {c['sharpe']:.2f} DD {c['max_drawdown']:.0%} | steady {row['steady']['cagr']:+.1%} Sh {row['steady']['sharpe']:.2f} DD {row['steady']['max_drawdown']:.0%} | "
            f"full {f['cagr']:+.1%} Sh {f['sharpe']:.2f} DD {f['max_drawdown']:.0%} [{row['minutes']:.1f} min]")


# What happened to an idea after the lab, where the lab verdict alone would mislead.
NOTES = {
    "risk": "better here, then failed the months since 2025 (rebuilt with three models: 5% a year against the market's 19%). Not live.",
    "earn_risk": "better here; same idea as the row below, which failed the months since 2025. Not live.",
}


def adopted() -> str | None:
    """Which lab variant matches what the live forecaster is built from (config.TARGET and EVENT_FAMILIES)."""
    want_use = sorted(["price", *EVENT_FAMILIES])
    want_target = None if TARGET == "plain" else TARGET
    for name, v in VARIANTS.items():
        if sorted(v["use"]) == want_use and v.get("targets") == want_target and not v.get("spec") and not v.get("wide"):
            return name
    return None


def regrade(log=print) -> None:
    """Grade every saved set of scores again (after the grading itself gained a column), without retraining anything."""
    panel, sectors, _ = pit.pit_panel(lambda m: None)
    fwd = {h: F.forward_returns(panel, h) for h in (5, 21)}
    for path in sorted(LAB.glob("scores_*.parquet")):
        name = path.stem[len("scores_"):]
        card = LAB / f"{name}.json"
        if name not in VARIANTS or not card.exists():
            continue
        old = json.loads(card.read_text())
        row = {**old, **grade(pd.read_parquet(path), panel, sectors, fwd), "label": VARIANTS[name]["label"]}
        card.write_text(json.dumps(research._clean(row)))
        log(f"{name:<14} steady {row['steady']['cagr']:+.1%} Sh {row['steady']['sharpe']:.2f} DD {row['steady']['max_drawdown']:.0%} | full {row['full']['cagr']:+.1%} Sh {row['full']['sharpe']:.2f} DD {row['full']['max_drawdown']:.0%} | skill {row['ic_week']:.4f}")


def collect() -> dict:
    """Gather every finished variant into one file for the app and the explainer."""
    rows = [json.loads(p.read_text()) for p in sorted(LAB.glob("*.json")) if p.stem in VARIANTS]
    rows.sort(key=lambda r: list(VARIANTS).index(r["name"]))
    out = {"period": ["2010-01-04", END], "rows": rows, "adopted": adopted(), "notes": NOTES}
    (RESEARCH / "lab.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--list"]:
        for k, v in VARIANTS.items():
            print(f"{k:<14} {v['label']}")
    elif args == ["--collect"]:
        print(len(collect()["rows"]), "variants collected")
    elif args == ["--regrade"]:
        regrade(lambda m: print(m, flush=True))
        print(len(collect()["rows"]), "variants collected; live model matches:", adopted())
    else:
        run(args or ["base"], lambda m: print(m, flush=True))
        collect()
