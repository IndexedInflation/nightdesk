"""Can the forecaster be made better? A fixed menu of changes, each tried on its own.

Every variant is graded the same way: year-by-year on 2010-2024 only (never the exam years),
with one model per horizon to keep it quick. A change is only worth keeping if it clearly
beats the baseline here.

    .venv/bin/python -m nightdesk.experiments
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

import pandas as pd

from . import backtest as B
from . import features as F
from . import research, universe
from .config import HOLD_BUFFER, RESEARCH, SCORE_SMOOTHING
from .model import ModelSpec
from .portfolio import smooth_scores

END = "2024-12-31"
VARIANTS: dict[str, dict] = {
    "baseline (what is running now)": {},
    "twice the trees, learning half as fast": {"n_trees": 600, "learning_rate": 0.025},
    "four horizons: day, week, month, quarter": {"horizons": (1, 5, 21, 63)},
    "learn the ordering directly (ranking objective)": {"objective": "rank"},
    "risk-adjusted answers": {"_targets": "risk"},
    "recent years count more (half-life 6 years)": {"decay_years": 6.0},
    "finer rules (smaller leaves)": {"min_leaf": 500, "num_leaves": 63},
    "month horizon only": {"horizons": (21,)},
}


SECOND_BATCH: dict[str, dict] = {
    "nine more measurements (industry detail, steadier momentum, 2-3 year moves)": {"_features": "extended"},
    "answers measured against each stock's own industry": {"_targets": "sector"},
    "both of the above": {"_features": "extended", "_targets": "sector"},
}


def main(batch: int = 1) -> None:
    """batch 1 and 2: the first runs, on today's list (flattered). batch 3: every variant again on the fair list."""
    log = lambda m: print(m, flush=True)
    fair = batch == 3
    if fair:
        from . import pit
        panel, sectors, member = pit.pit_panel(lambda m: None)
        params = pit.recipe()
    else:
        panel, sectors, member = research.load_research_panel(), universe.sector_map(), None
        params = B.PortfolioParams(hold_buffer=HOLD_BUFFER)
    industries = {r["symbol"]: r["industry"] for r in universe.load_universe()}
    ds = F.build_dataset(panel, sectors, horizons=(1, 5, 21, 63), risk_adjusted=True, membership=member)
    ds_more = F.build_dataset(panel, sectors, horizons=(1, 5, 21, 63), risk_adjusted=True, industries=industries, membership=member) if batch >= 2 else None
    spy = panel["adj"]["SPY"].pct_change(fill_method=None)
    fwd = {h: F.forward_returns(panel, h) for h in (1, 5, 21)}
    out_file = RESEARCH / {1: "experiments.json", 2: "experiments2.json", 3: "experiments_fair.json"}[batch]
    menu = VARIANTS if batch == 1 else SECOND_BATCH if batch == 2 else {**VARIANTS, **SECOND_BATCH}
    rows = []
    for name, change in menu.items():
        t0 = time.time()
        change = dict(change)
        base = ds
        if change.pop("_features", None) == "extended":
            ds = ds_more
        kind = change.pop("_targets", None)
        targets = ds.y_risk if kind == "risk" else ds.y_sector if kind == "sector" else None
        spec = replace(ModelSpec(seeds=(11,)), **change)
        scores = research.walk_forward(ds, spec, 2010, END, log=lambda m: None, targets=targets)
        ic = {h: B.information_coefficient(scores, fwd[h]) for h in (1, 5, 21)}
        buckets = B.bucket_returns(scores, fwd[5])
        res = B.simulate(smooth_scores(scores, SCORE_SMOOTHING), panel, params, sectors)
        m = B.metrics(res.returns, spy.reindex(res.returns.index).fillna(0.0))
        row = {"name": name, "ic1": ic[1]["mean"], "ic5": ic[5]["mean"], "ic21": ic[21]["mean"], "ic5_t": ic[5]["t_stat"], "ic21_t": ic[21]["t_stat"],
               "ic5_se": ic[5]["std"] / len(scores) ** 0.5, "spread_week_bps": (buckets[-1] - buckets[0]) * 1e4,
               "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"],
               "turnover": float(res.turnover.sum() / (len(scores) / 252)), "minutes": (time.time() - t0) / 60}
        rows.append(row)
        ds = base
        log(f"{name:<50} IC week {row['ic5']:.4f} (t {row['ic5_t']:.1f})  IC month {row['ic21']:.4f}  top-bottom {row['spread_week_bps']:.0f}bps/wk  "
            f"recipe: {row['cagr']:+.1%} Sharpe {row['sharpe']:.2f} DD {row['max_drawdown']:.0%}  [{row['minutes']:.1f} min]")
        out_file.write_text(json.dumps(research._clean({"period": ["2010-01-04", END], "fair_list": fair, "rows": rows})))


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
