"""Honesty check: is a model variant doing more than "own the calmest stocks"?

Builds no-model portfolios on the fair list (2010 to 2024) with the same rules, and compares:
  - the 50 calmest stocks (lowest 63-day swings), and
  - lab variants' scores,
year by year, so one lucky stretch can't carry the result.

    .venv/bin/python tools/calm_check.py base earn_risk
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from nightdesk import backtest as B, features as F, pit      # noqa: E402
from nightdesk.config import RESEARCH, SCORE_SMOOTHING        # noqa: E402
from nightdesk.portfolio import smooth_scores                 # noqa: E402

END = "2024-12-31"
panel, sectors, member = pit.pit_panel(lambda m: None)
spy = panel["adj"]["SPY"].pct_change(fill_method=None)
r = panel["adj"].pct_change(fill_method=None)
vol63 = r.rolling(63).std()
mask = member.reindex(index=panel["close"].index, columns=panel["close"].columns, fill_value=False)
calm = (-vol63).where(mask).loc["2010-01-04":END].dropna(how="all")
sets = {"calm": calm}                                         # no model at all: just the 50 least jumpy stocks
for name in sys.argv[1:]:                                     # "pit" = the live model's own test scores; anything else = a lab variant
    path = RESEARCH / "scores_pit.parquet" if name == "pit" else RESEARCH / "lab" / f"scores_{name}.parquet"
    sets["model" if name == "pit" else name] = smooth_scores(pd.read_parquet(path), SCORE_SMOOTHING).loc["2010-01-04":END]

MODES = {k: pit.recipe(k) for k in ("cautious", "steady", "full")}
out = {}
for label, scores in sets.items():
    row = {}
    for mode, prm in MODES.items():
        res = B.simulate(scores, panel, prm, sectors)
        m = B.metrics(res.returns, spy.reindex(res.returns.index).fillna(0.0))
        yearly = (1 + res.returns).groupby(res.returns.index.year).prod() - 1
        spy_y = (1 + spy.reindex(res.returns.index).fillna(0.0)).groupby(res.returns.index.year).prod() - 1
        half = len(res.returns) // 2
        h1 = B.metrics(res.returns.iloc[:half], spy.reindex(res.returns.index).fillna(0.0).iloc[:half])
        h2 = B.metrics(res.returns.iloc[half:], spy.reindex(res.returns.index).fillna(0.0).iloc[half:])
        row[mode] = {**{k: float(m[k]) for k in ("cagr", "sharpe", "max_drawdown", "vol", "beta", "alpha")}, "turnover": float(res.turnover.sum() / (len(scores) / 252)),
                     "years_beat_spy": int((yearly > spy_y).sum()), "years": int(len(yearly)), "first_half": {k: float(h1[k]) for k in ("cagr", "sharpe")},
                     "second_half": {k: float(h2[k]) for k in ("cagr", "sharpe")}, "yearly": {int(y): float(v) for y, v in yearly.items()}}
        print(f"{label:<36} {mode:<9} {m['cagr']:+.1%} Sh {m['sharpe']:.2f} DD {m['max_drawdown']:.0%} beta {m['beta']:.2f} alpha {m['alpha']:+.1%} turn {row[mode]['turnover']:.0f}x | beat S&P {row[mode]['years_beat_spy']}/{len(yearly)} yrs | "
              f"1st half {h1['cagr']:+.1%} Sh {h1['sharpe']:.2f}, 2nd half {h2['cagr']:+.1%} Sh {h2['sharpe']:.2f}", flush=True)
    out[label] = row
sm = B.metrics(spy.loc[calm.index[0]:END].fillna(0.0), spy.loc[calm.index[0]:END].fillna(0.0))
out["spy"] = {k: float(sm[k]) for k in ("cagr", "sharpe", "max_drawdown", "vol")}
print(f"S&P 500: {sm['cagr']:+.1%} Sh {sm['sharpe']:.2f} DD {sm['max_drawdown']:.0%}")
(RESEARCH / "lab" / "calm_check.json").write_text(json.dumps(out))
