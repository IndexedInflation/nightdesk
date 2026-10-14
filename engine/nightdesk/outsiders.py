"""The outsider test: does the forecaster work on companies it has never seen?

The model is trained only on the S&P 500 (big companies). Here it scores the S&P MidCap 400
(about 400 mid-sized companies) year by year, without ever training on them. If its scores
still line up with what those stocks did next, it learnt something general about how stocks
behave, not quirks of the 500 names it studied.

    .venv/bin/python -m nightdesk.outsiders
"""
from __future__ import annotations

import json
import time

import httpx
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

from . import backtest as B
from . import data, features as F
from . import research, universe
from .config import CACHE, HISTORY_START, HOLD_BUFFER, RESEARCH, SCORE_SMOOTHING
from .model import Ensemble, ModelSpec
from .portfolio import smooth_scores

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"


def midcaps() -> list[dict]:
    path = CACHE / "midcaps.json"
    if path.exists():
        return json.loads(path.read_text())
    html = httpx.get(URL, headers={"User-Agent": "Nightdesk/1.0 (personal research app)"}, timeout=30, follow_redirects=True).text
    table = BeautifulSoup(html, "html.parser").find("table", {"id": "constituents"})
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 3:
            rows.append({"symbol": cells[0].upper().replace(".", "-"), "name": cells[1], "sector": cells[2]})
    if len(rows) < 300:
        raise RuntimeError(f"only {len(rows)} mid-sized companies parsed")
    path.write_text(json.dumps(rows))
    return rows


def mid_panel(log=print) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    rows = midcaps()
    big = set(universe.symbols())
    syms = sorted({r["symbol"] for r in rows} - big)                    # nothing the model trained on
    paths = {f: CACHE / f"mid_{f}.parquet" for f in data.FIELDS}
    if not all(p.exists() for p in paths.values()):
        log(f"downloading history for {len(syms)} mid-sized companies")
        panel = data._clean(data._download(syms, HISTORY_START, log=log))
        for f, df in panel.items():
            df.to_parquet(paths[f])
    panel = {f: pd.read_parquet(p) for f, p in paths.items()}
    main = research.load_research_panel()
    days = main["close"].index
    out = {}
    for f in data.FIELDS:                                                # same calendar, plus SPY so "the market" means the same thing
        df = panel[f].reindex(days)
        df["SPY"] = main[f]["SPY"]
        out[f] = df
    return out, {r["symbol"]: r["sector"] for r in rows}


def run(log=print) -> dict:
    main, sectors = research.load_research_panel(), universe.sector_map()
    mid, mid_sectors = mid_panel(log)
    spec = ModelSpec(seeds=(11,))
    big = F.build_dataset(main, sectors, horizons=spec.horizons)
    out_ds = F.build_dataset(mid, mid_sectors, horizons=spec.horizons)
    log(f"training rows (big companies): {len(big.dates):,}; outsider rows to score: {len(out_ds.dates):,}; outsiders: {len(set(out_ds.symbols))}")
    days = np.unique(big.dates)
    scores = np.full(len(out_ds.dates), np.nan)
    for year in range(2010, pd.Timestamp(days[-1]).year + 1):
        t0 = time.time()
        test = (out_ds.dates >= np.datetime64(f"{year}-01-01")) & (out_ds.dates <= np.datetime64(f"{year}-12-31"))
        if not test.any():
            continue
        first = np.searchsorted(days, out_ds.dates[test].min())
        row_ok = {h: big.dates <= days[max(0, first - h - 1)] for h in spec.horizons}
        model = Ensemble(spec, big.feature_names).fit(big.X, big.y, row_ok, big.dates)
        scores[test] = model.score(out_ds.X[test], out_ds.dates[test])
        log(f"  {year}: scored {int(test.sum()):,} outsider rows in {time.time() - t0:.0f}s")
    table = out_ds.frame(scores).dropna(how="all")
    table.to_parquet(RESEARCH / "scores_outsiders.parquet")
    result = {"companies": int(table.shape[1]), "periods": {}}
    spy = main["adj"]["SPY"].pct_change(fill_method=None)
    mask = F.universe_mask(mid)[table.columns]
    for label, lo, hi in (("rehearsal", "2010-01-01", "2024-12-31"), ("exam", "2025-01-01", None)):
        part = table.loc[lo:hi] if hi else table.loc[lo:]
        ic = {h: B.information_coefficient(part, F.forward_returns(mid, h)) for h in (5, 21)}
        buckets = B.bucket_returns(part, F.forward_returns(mid, 5))
        blended = smooth_scores(table, SCORE_SMOOTHING).loc[part.index[0]: part.index[-1]]
        res = B.simulate(blended, mid, B.PortfolioParams(hold_buffer=HOLD_BUFFER), mid_sectors)
        ew = research.universe_benchmark(mid, mask).reindex(part.index).fillna(0.0)
        m, mu, ms = B.metrics(res.returns, spy.reindex(part.index).fillna(0.0)), B.metrics(ew), B.metrics(spy.reindex(part.index).fillna(0.0))
        result["periods"][label] = {
            "start": str(part.index[0].date()), "end": str(part.index[-1].date()),
            "ic_week": ic[5]["mean"], "ic_week_t": ic[5]["t_stat"], "ic_week_positive": ic[5]["positive_days"], "ic_month": ic[21]["mean"], "ic_month_t": ic[21]["t_stat"],
            "buckets_week_bps": [b * 1e4 for b in buckets],
            "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"],
            "universe_cagr": mu["cagr"], "universe_sharpe": mu["sharpe"], "universe_max_drawdown": mu["max_drawdown"], "spy_cagr": ms["cagr"]}
        p = result["periods"][label]
        log(f"{label}: IC week {p['ic_week']:.4f} (t {p['ic_week_t']:.1f}), IC month {p['ic_month']:.4f} | recipe {p['cagr']:+.1%} Sharpe {p['sharpe']:.2f} DD {p['max_drawdown']:.0%} "
            f"| all outsiders equally {p['universe_cagr']:+.1%} DD {p['universe_max_drawdown']:.0%} | S&P 500 {p['spy_cagr']:+.1%}")
    (RESEARCH / "outsiders.json").write_text(json.dumps(research._clean(result)))
    return result


if __name__ == "__main__":
    run(lambda m: print(m, flush=True))
