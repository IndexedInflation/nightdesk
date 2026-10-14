"""On the fair list, 2010-2024: how should the rankings be turned into a portfolio?
Takes a scores file from the lab and tries a grid of portfolio rules. Reports every row.

    .venv/bin/python tools/portfolio_grid.py base
"""
import json, sys, time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import pandas as pd
from nightdesk import backtest as B, pit, research
from nightdesk.config import RESEARCH
from nightdesk.portfolio import smooth_scores

name = sys.argv[1] if len(sys.argv) > 1 else "base"
panel, sectors, member = pit.pit_panel(lambda m: None)
scores = pd.read_parquet(RESEARCH / "lab" / f"scores_{name}.parquet")
spy = panel["adj"]["SPY"].pct_change(fill_method=None)
rows = []
MODES = {"cautious (cash in storms, halve in downtrend)": {}, "halve in downtrend only": {"target_vol": None},
         "brake only in a real storm (25%)": {"target_vol": 0.25, "trend_filter": False}, "always fully invested": {"target_vol": None, "trend_filter": False}}
for smooth in (1, 3, 10):
    sm = smooth_scores(scores, smooth)
    for k in (20, 50):
        for buf in (1.5, 4.0):
            for mode, ch in MODES.items():
                prm = replace(B.PortfolioParams(max_positions=k, hold_buffer=buf), **ch)
                res = B.simulate(sm, panel, prm, sectors)
                m = B.metrics(res.returns, spy.reindex(res.returns.index).fillna(0.0))
                d = B.metrics(B.simulate(sm, panel, replace(prm, cost_bps=10.0), sectors).returns)
                rows.append({"blend_days": smooth, "stocks": k, "keep_while_top": int(k * buf), "mode": mode, "cagr": m["cagr"], "sharpe": m["sharpe"],
                             "max_drawdown": m["max_drawdown"], "beta": m["beta"], "alpha": m["alpha"], "turnover": float(res.turnover.sum() / (len(sm) / 252)),
                             "cagr_double_cost": d["cagr"], "sharpe_double_cost": d["sharpe"]})
                r = rows[-1]
                print(f"blend {smooth:>2}  {k:>3} stocks keep<{r['keep_while_top']:<3} {mode:<46} {r['cagr']:+.1%} Sh {r['sharpe']:.2f} DD {r['max_drawdown']:.0%} beta {r['beta']:.2f} turn {r['turnover']:.0f}x | 2x cost {r['cagr_double_cost']:+.1%} Sh {r['sharpe_double_cost']:.2f}", flush=True)
b = B.metrics(spy.reindex(scores.index).fillna(0.0))
(RESEARCH / "lab" / f"grid_{name}.json").write_text(json.dumps(research._clean({"scores": name, "rows": rows, "spy": {"cagr": b["cagr"], "sharpe": b["sharpe"], "max_drawdown": b["max_drawdown"]}})))
print(f"S&P 500: {b['cagr']:+.1%} Sh {b['sharpe']:.2f} DD {b['max_drawdown']:.0%}")
