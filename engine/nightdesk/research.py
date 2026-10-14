"""Research: train on the past, test on the years that follow, and write down what happened.

"Walk-forward" means: train on everything before 2010, test on 2010. Then train on everything
before 2011, test on 2011. And so on. Every test year is data the model had never seen, so
the stitched-together result is an honest rehearsal of running the strategy for real.

Run from the project folder:
    .venv/bin/python -m nightdesk.research walk  --name v1 --end 2024-12-31
    .venv/bin/python -m nightdesk.research grade --name v1 --end 2024-12-31
    .venv/bin/python -m nightdesk.research fit   --name v1
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from datetime import datetime

import numpy as np
import pandas as pd

from . import backtest as B
from . import data, features as F, mktcal, universe
from .config import HOLD_BUFFER, MODELS, RESEARCH, SCORE_SMOOTHING
from .model import Ensemble, ModelSpec
from .portfolio import smooth_scores


def load_research_panel(now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """Cached prices on real trading days only, without today's unfinished session."""
    panel = data.load_panel()
    days = data.trading_days(panel)
    done = pd.Timestamp(mktcal.last_completed_session(now))
    saved = data.cache_saved_at()
    if saved is not None:                                   # a day saved before its closing bell is half a day: leave it out
        done = min(done, pd.Timestamp(mktcal.last_completed_session(datetime.fromtimestamp(saved, mktcal.NY))))
    days = days[days <= done]
    return {f: df.reindex(days) for f, df in panel.items()}


def walk_forward(ds: F.Dataset, spec: ModelSpec, first_year: int = 2010, end: str | None = None,
                 log=print, targets: dict | None = None) -> pd.DataFrame:
    """Out-of-sample scores, one test year at a time. Returns days x stocks.
    `targets` swaps in a different answer sheet for training (grading always uses real returns)."""
    y_train = targets or ds.y
    days = np.unique(ds.dates)
    end_ts = np.datetime64(pd.Timestamp(end)) if end else days[-1]
    last_year = pd.Timestamp(end_ts).year
    scores = np.full(len(ds.dates), np.nan)
    for year in range(first_year, last_year + 1):
        t0 = time.time()
        lo, hi = np.datetime64(f"{year}-01-01"), min(np.datetime64(f"{year}-12-31"), end_ts)
        test = (ds.dates >= lo) & (ds.dates <= hi)
        if not test.any():
            continue
        first_test = np.searchsorted(days, ds.dates[test].min())
        row_ok = {}
        for h in spec.horizons:                 # purge: a training answer may not reach into the test year
            cutoff = days[max(0, first_test - h - 1)]
            row_ok[h] = ds.dates <= cutoff
        model = Ensemble(spec, ds.feature_names).fit(ds.X, y_train, row_ok, ds.dates)
        scores[test] = model.score(ds.X[test], ds.dates[test])
        log(f"  {year}: trained on {model.meta[f'rows_h{spec.horizons[0]}']:,} rows, "
            f"scored {int(test.sum()):,} rows in {time.time() - t0:.0f}s")
    return ds.frame(scores).dropna(how="all")


def universe_benchmark(panel: dict[str, pd.DataFrame], mask: pd.DataFrame) -> pd.Series:
    """Daily return of owning every eligible stock in equal amounts (the fair yardstick)."""
    r = panel["adj"][mask.columns].pct_change(fill_method=None)
    return r.where(mask.shift(1, fill_value=False)).mean(axis=1).fillna(0.0)


def _clean(obj):
    """Make numbers JSON-safe (no NaN, no numpy types)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else round(float(obj), 6)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def grade(scores: pd.DataFrame, panel: dict[str, pd.DataFrame], sectors: dict[str, str],
          params: B.PortfolioParams, label: str = "", mask: pd.DataFrame | None = None) -> dict:
    """Everything worth knowing about how a set of scores would have traded."""
    days = scores.index
    mask = (F.universe_mask(panel) if mask is None else mask)[scores.columns]      # the yardstick uses exactly the stocks being scored
    spy = panel["adj"]["SPY"].pct_change(fill_method=None).reindex(days).fillna(0.0)
    ew = universe_benchmark(panel, mask).reindex(days).fillna(0.0)
    res = B.simulate(scores, panel, params, sectors)
    r = res.returns
    out = {
        "label": label, "start": str(days[0].date()), "end": str(days[-1].date()), "params": asdict(params),
        "strategy": B.metrics(r, spy), "spy": B.metrics(spy), "universe": B.metrics(ew, spy),
        "vs_universe": {k: v for k, v in B.metrics(r, ew).items() if k in ("alpha", "beta", "info_ratio", "excess_cagr", "corr")},
        "turnover_per_year": float(res.turnover.sum() / (len(days) / 252)),
        "avg_exposure": float(res.exposure.mean()), "avg_holdings": float(res.n_holdings[res.n_holdings > 0].mean()),
    }
    weekly = slice(None, None, 5)
    eq = lambda s: (1 + s).cumprod().iloc[weekly].round(4).tolist()
    out["curve"] = {"dates": [str(d.date()) for d in days[weekly]], "strategy": eq(r), "spy": eq(spy), "universe": eq(ew)}
    dd = lambda s: ((1 + s).cumprod() / np.maximum(1.0, (1 + s).cumprod().cummax()) - 1).iloc[weekly].round(4).tolist()
    out["drawdown"] = {"strategy": dd(r), "spy": dd(spy)}
    yearly = []
    for year, grp in r.groupby(r.index.year):
        idx = grp.index
        yearly.append({"year": int(year), "strategy": float((1 + grp).prod() - 1),
                       "spy": float((1 + spy[idx]).prod() - 1), "universe": float((1 + ew[idx]).prod() - 1)})
    out["yearly"] = yearly
    ic = {}
    for h in (1, 5, 21):
        stat = B.information_coefficient(scores, F.forward_returns(panel, h))
        series = stat.pop("series")
        stat["by_year"] = {int(y): float(v) for y, v in series.groupby(series.index.year).mean().items()}
        ic[f"h{h}"] = stat
    out["ic"] = ic
    out["accuracy"] = {"week": B.pick_accuracy(scores, F.forward_returns(panel, 5)), "month": B.pick_accuracy(scores, F.forward_returns(panel, 21)),
                       "day": B.pick_accuracy(scores, F.forward_returns(panel, 1))}
    out["buckets_next_day_bps"] = [b * 1e4 for b in B.bucket_returns(scores, F.forward_returns(panel, 1))]
    out["buckets_next_week_bps"] = [b * 1e4 for b in B.bucket_returns(scores, F.forward_returns(panel, 5))]
    sens = []
    for bps in (0.0, 5.0, 10.0, 20.0):
        m = B.metrics(B.simulate(scores, panel, replace(params, cost_bps=bps), sectors).returns, spy)
        sens.append({"cost_bps": bps, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"]})
    out["cost_sensitivity"] = sens
    other = "next_open" if params.execution == "close" else "close"
    m = B.metrics(B.simulate(scores, panel, replace(params, execution=other), sectors).returns, spy)
    out["other_execution"] = {"execution": other, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"]}
    return _clean(out)


VARIANTS = {   # a small, fixed menu decided before looking: each changes ONE thing from the base recipe
    "base: 20 stocks, keep while in top 50": {},
    "keep while in top 80": {"hold_buffer": 4.0},
    "keep while in top 120": {"hold_buffer": 6.0},
    "30 stocks": {"max_positions": 30},
    "10 stocks": {"max_positions": 10, "max_weight": 0.12},
    "always fully invested (no cash rules)": {"target_vol": None, "trend_filter": False},
    "3-day blended scores": {"_smooth": 3},
    "5-day blended scores, keep while in top 80": {"_smooth": 5, "hold_buffer": 4.0},
    "10-day blended scores, keep while in top 80": {"_smooth": 10, "hold_buffer": 4.0},
}


def run_variants(scores: pd.DataFrame, panel, sectors, log=print) -> list[dict]:
    days = scores.index
    spy = panel["adj"]["SPY"].pct_change(fill_method=None).reindex(days).fillna(0.0)
    ew = universe_benchmark(panel, F.universe_mask(panel)).reindex(days).fillna(0.0)
    out = []
    for name, change in VARIANTS.items():
        change = dict(change)
        sc = smooth_scores(scores, change.pop("_smooth", 1))
        params = replace(B.PortfolioParams(), **change)
        res = B.simulate(sc, panel, params, sectors)
        m, mu = B.metrics(res.returns, spy), B.metrics(res.returns, ew)
        slow = B.metrics(B.simulate(sc, panel, replace(params, cost_bps=10.0), sectors).returns)
        nxt = B.metrics(B.simulate(sc, panel, replace(params, execution="next_open"), sectors).returns)
        row = {"name": name, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"], "vol": m["vol"],
               "alpha_vs_universe": mu["alpha"], "beta": m["beta"], "turnover": float(res.turnover.sum() / (len(days) / 252)),
               "exposure": float(res.exposure.mean()), "sharpe_at_double_cost": slow["sharpe"], "cagr_at_double_cost": slow["cagr"],
               "sharpe_next_open": nxt["sharpe"], "cagr_next_open": nxt["cagr"]}
        out.append(row)
        log(f"  {name:<46} CAGR {row['cagr']:+.1%}  Sharpe {row['sharpe']:.2f}  maxDD {row['max_drawdown']:.0%}  turnover {row['turnover']:.0f}x  "
            f"expo {row['exposure']:.0%}  | 2x cost: {row['cagr_at_double_cost']:+.1%} / {row['sharpe_at_double_cost']:.2f}  | next open: {row['cagr_next_open']:+.1%} / {row['sharpe_next_open']:.2f}")
    return _clean(out)


def placebo(scores: pd.DataFrame, panel, sectors, start: str | None = None, runs: int = 30, seed: int = 0, log=print,
            end: str | None = None, params: B.PortfolioParams | None = None) -> dict:
    """The luck test. Keep every rule the same but shuffle the scores at random each day, many times.
    If the real scores are no better than the shuffled ones, the "skill" was just the rules and the market."""
    recipe = params or B.PortfolioParams(hold_buffer=HOLD_BUFFER)

    def run(table: pd.DataFrame) -> dict:
        sm = smooth_scores(table, SCORE_SMOOTHING)
        if start:
            sm = sm.loc[start:]
        if end:
            sm = sm.loc[:end]
        m = B.metrics(B.simulate(sm, panel, recipe, sectors).returns)
        return {"cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"]}

    actual = run(scores)
    rng = np.random.default_rng(seed)
    values, shuffled = scores.to_numpy(), []
    for i in range(runs):
        fake = values.copy()
        for row in fake:                                   # same stocks eligible each day, scores dealt out at random
            ok = ~np.isnan(row)
            row[ok] = rng.permutation(row[ok])
        shuffled.append(run(pd.DataFrame(fake, index=scores.index, columns=scores.columns)))
        log(f"  shuffle {i + 1}/{runs}: {shuffled[-1]['cagr']:+.1%} a year, Sharpe {shuffled[-1]['sharpe']:.2f}")
    cagrs = sorted(r["cagr"] for r in shuffled)
    return _clean({"actual": actual, "runs": runs, "shuffled": shuffled,
                   "shuffled_low": cagrs[0], "shuffled_mid": cagrs[len(cagrs) // 2], "shuffled_high": cagrs[-1],
                   "beaten_cagr": sum(actual["cagr"] > r["cagr"] for r in shuffled),
                   "beaten_sharpe": sum(actual["sharpe"] > r["sharpe"] for r in shuffled),
                   "start": str((scores.loc[start:] if start else scores).index[0].date()), "end": str(scores.index[-1].date())})


def summary_line(g: dict) -> str:
    s, b, u = g["strategy"], g["spy"], g["universe"]
    return (f"{g['label']:<26} {g['start']}..{g['end']}  CAGR {s['cagr']:+.1%} (SPY {b['cagr']:+.1%}, universe {u['cagr']:+.1%})  "
            f"Sharpe {s['sharpe']:.2f} (SPY {b['sharpe']:.2f})  maxDD {s['max_drawdown']:.0%} (SPY {b['max_drawdown']:.0%})  "
            f"IR vs universe {g['vs_universe']['info_ratio']:.2f}  IC5 {g['ic']['h5']['mean']:.4f} t={g['ic']['h5']['t_stat']:.1f}  "
            f"turnover {g['turnover_per_year']:.1f}x  expo {g['avg_exposure']:.0%}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Nightdesk research")
    ap.add_argument("cmd", choices=["walk", "grade", "fit", "variants", "card", "placebo"])
    ap.add_argument("--name", default="v1")
    ap.add_argument("--end", default=None, help="last test day, e.g. 2024-12-31")
    ap.add_argument("--first-year", type=int, default=2010)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--start", default=None, help="first day to grade, e.g. 2025-01-01")
    ap.add_argument("--out", default=None, help="name of the grade file to write")
    ap.add_argument("--members-before", default=None, help="only companies added to the index on or before this date")
    args = ap.parse_args()
    if args.cmd == "card":
        from . import explain
        card = explain.model_card(Ensemble.load(MODELS / "production.json"))
        (RESEARCH / "model_card.json").write_text(json.dumps(_clean(card)))
        print("families:", [(f["name"], round(f["share"], 3)) for f in card["families"]])
        print("top:", [(t["feature"], round(t["share"], 3)) for t in card["top"][:10]])
        return
    panel = load_research_panel()
    sectors = universe.sector_map()
    spec = ModelSpec(seeds=tuple([11, 22, 33, 44, 55][: args.seeds]))
    if args.cmd == "walk":
        t0 = time.time()
        ds = F.build_dataset(panel, sectors, horizons=spec.horizons)
        print(f"dataset: {ds.X.shape[0]:,} rows x {ds.X.shape[1]} signals in {time.time() - t0:.0f}s", flush=True)
    if args.cmd == "walk":
        scores = walk_forward(ds, spec, args.first_year, args.end, log=lambda m: print(m, flush=True))
        scores.to_parquet(RESEARCH / f"scores_{args.name}.parquet")
        print("saved", scores.shape)
    elif args.cmd == "grade":
        scores = pd.read_parquet(RESEARCH / f"scores_{args.name}.parquet")
        if args.end:
            scores = scores.loc[: args.end]
        if args.members_before:                                             # robustness: only companies already in the index by then
            cutoff = args.members_before
            keep = [r["symbol"] for r in universe.load_universe() if r["added"] and r["added"] <= cutoff]
            scores = scores[[c for c in scores.columns if c in set(keep)]]
            print(f"keeping {scores.shape[1]} companies that were already in the S&P 500 on {cutoff}")
        blended = smooth_scores(scores, SCORE_SMOOTHING)                    # blend first, then cut: the live engine always has earlier days to blend with
        if args.start:
            blended = blended.loc[args.start:]
        recipe = B.PortfolioParams(hold_buffer=HOLD_BUFFER)                  # the frozen recipe, same as the live engine
        g = grade(blended, panel, sectors, recipe, label=args.name)
        g["recipe"] = {"hold_buffer": HOLD_BUFFER, "score_smoothing": SCORE_SMOOTHING}
        (RESEARCH / f"grade_{args.out or args.name}.json").write_text(json.dumps(g))
        print(summary_line(g))
    elif args.cmd == "placebo":
        scores = pd.read_parquet(RESEARCH / f"scores_{args.name}.parquet")
        if args.end:
            scores = scores.loc[: args.end]
        out = placebo(scores, panel, sectors, start=args.start, log=lambda m: print(m, flush=True))
        (RESEARCH / f"placebo_{args.out or args.name}.json").write_text(json.dumps(out))
        print(f"real scores {out['actual']['cagr']:+.1%} a year; shuffled scores {out['shuffled_low']:+.1%} to {out['shuffled_high']:+.1%} "
              f"(middle {out['shuffled_mid']:+.1%}); real beat {out['beaten_cagr']} of {out['runs']}")
    elif args.cmd == "variants":
        scores = pd.read_parquet(RESEARCH / f"scores_{args.name}.parquet")
        if args.end:
            scores = scores.loc[: args.end]
        rows = run_variants(scores, panel, sectors, log=lambda m: print(m, flush=True))
        (RESEARCH / "variants.json").write_text(json.dumps({"period": [str(scores.index[0].date()), str(scores.index[-1].date())], "rows": rows}))
    elif args.cmd == "fit":
        from . import pit                                   # train on the fair list (the index as it really was)
        model = pit.fit_production(spec, log=lambda m: print(m, flush=True))
        print("saved production model", model.meta)


if __name__ == "__main__":
    main()
