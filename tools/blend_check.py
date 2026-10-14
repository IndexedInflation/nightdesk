"""Would averaging the two forecasters (plain answers + risk-adjusted answers) be steadier than either?

Uses saved scores, plus a quick plain-answers run for 2025 onward, and prints every setting for both
periods. Writes research/lab/blend_check.json.

    .venv/bin/python tools/blend_check.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from nightdesk import backtest as B, pit, research          # noqa: E402
from nightdesk.config import RESEARCH, SCORE_SMOOTHING       # noqa: E402
from nightdesk.model import ModelSpec                        # noqa: E402
from nightdesk.portfolio import smooth_scores                # noqa: E402

spec = ModelSpec(seeds=(11,))
panel, sectors, member, ds = pit.dataset(spec, lambda m: None)
spy = panel["adj"]["SPY"].pct_change(fill_method=None)
plain_late = research.walk_forward(ds, spec, 2025, None, log=lambda m: print(m, flush=True), targets=ds.y)
plain = pd.concat([pd.read_parquet(RESEARCH / "lab" / "scores_base.parquet"), plain_late]).sort_index()
plain = plain[~plain.index.duplicated()]
risk = pd.read_parquet(RESEARCH / "candidate_risk" / "scores.parquet").reindex(index=plain.index, columns=plain.columns)
z = lambda df: df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)
sets = {"plain": plain, "risk": risk, "blend": (z(plain) + z(risk)) / 2}
out = {}
for name, scores in sets.items():
    smooth = smooth_scores(scores, SCORE_SMOOTHING)
    out[name] = {}
    for label, lo, hi in (("rehearsal", "2010-01-01", "2024-12-31"), ("exam", "2025-01-01", None)):
        part = smooth.loc[lo:hi] if hi else smooth.loc[lo:]
        bench = spy.reindex(part.index).fillna(0.0)
        row = {}
        for mode in ("cautious", "steady", "full"):
            res = B.simulate(part, panel, pit.recipe(mode), sectors)
            m = B.metrics(res.returns, bench)
            row[mode] = {k: float(m[k]) for k in ("cagr", "sharpe", "max_drawdown", "beta", "alpha")}
        luck = research.placebo(scores, panel, sectors, start=lo, runs=30, log=lambda m: None, end=hi, params=pit.recipe("steady"))
        row["luck"] = {"beaten": int(luck["beaten_cagr"]), "runs": int(luck["runs"]), "low": float(luck["shuffled_low"]), "high": float(luck["shuffled_high"])}
        sm = B.metrics(bench)
        row["spy"] = {k: float(sm[k]) for k in ("cagr", "sharpe", "max_drawdown")}
        out[name][label] = row
        print(f"{name:<6} {label:<9} " + " | ".join(f"{k} {row[k]['cagr']:+.1%} Sh {row[k]['sharpe']:.2f} DD {row[k]['max_drawdown']:.0%}" for k in ("cautious", "steady", "full"))
              + f" | luck {row['luck']['beaten']}/{row['luck']['runs']} | S&P {sm['cagr']:+.1%} Sh {sm['sharpe']:.2f} DD {sm['max_drawdown']:.0%}", flush=True)
(RESEARCH / "lab" / "blend_check.json").write_text(json.dumps(out))
