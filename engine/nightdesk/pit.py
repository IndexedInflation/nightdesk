"""The fairer test: use the S&P 500 as it really was each year, not as it is today.

The first tests used today's 500 companies for every past year. That list is made of
survivors, which flatters any strategy (measured at about 5.6 points a year). This module
rebuilds the membership year by year from Wikipedia's record of which companies joined and
left, and adds back the price history of companies that were later dropped, where Yahoo
still has it. Companies that went bankrupt or were bought out are mostly gone from Yahoo,
so some flattery remains. It is measured again at the end against a real fund.

    .venv/bin/python -m nightdesk.pit
"""
from __future__ import annotations

from .providers import yahoo

import json
import time
import warnings
from datetime import datetime

import httpx
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

from . import backtest as B
from . import data, features as F, mktcal
from . import research, universe
from .config import (CACHE, DEFAULT_MODE, EVENT_FAMILIES, HISTORY_START, HOLD_BUFFER, MAX_POSITIONS, MODELS, RESEARCH, RISK_MODES,
                     SCORE_SMOOTHING, TARGET)
from .model import Ensemble, ModelSpec
from .portfolio import smooth_scores

URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
YAHOO_TO_GICS = {"Technology": "Information Technology", "Financial Services": "Financials", "Healthcare": "Health Care",
                 "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples", "Basic Materials": "Materials"}


def changes() -> list[dict]:
    """Every recorded change: {'date', 'added', 'removed'} (either side may be None). Refreshed monthly."""
    path = CACHE / "sp500_changes.json"
    if path.exists() and time.time() - path.stat().st_mtime < 30 * 86400:
        return json.loads(path.read_text())
    try:
        html = httpx.get(URL, headers={"User-Agent": "Nightdesk/1.0 (personal research app)"}, timeout=30, follow_redirects=True).text
    except Exception:
        if path.exists():
            return json.loads(path.read_text())               # an old record beats none
        raise
    table = BeautifulSoup(html, "html.parser").find("table", {"id": "changes"})
    out = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 5:
            continue
        try:
            day = datetime.strptime(cells[0], "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        sym = lambda s: s.upper().replace(".", "-") or None
        out.append({"date": day, "added": sym(cells[1]), "removed": sym(cells[3])})
    if len(out) < 300:
        raise RuntimeError(f"only {len(out)} index changes parsed")
    path.write_text(json.dumps(out))
    return out


def build_stints(events: list[dict], current: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """symbol -> [(joined, left), ...] as ISO dates ('0000-00-00' = before records, '9999-12-31' = still in)."""
    events = sorted(events, key=lambda e: e["date"])
    out: dict[str, list[tuple[str, str]]] = {}
    for row in current:                                               # today's members: in since their "date added"
        added = row["added"] if len(row["added"]) == 10 and row["added"][4] == "-" else "0000-00-00"
        out.setdefault(row["symbol"], []).append((added, "9999-12-31"))
    for e in events:                                                  # earlier spells that ended in a removal
        sym = e["removed"]
        if not sym or e["date"] < HISTORY_START:
            continue
        joins = [x["date"] for x in events if x["added"] == sym and x["date"] < e["date"]]
        spell = (max(joins) if joins else "0000-00-00", e["date"])
        if spell not in out.get(sym, []):
            out.setdefault(sym, []).append(spell)
    return out


def stints() -> dict[str, list[tuple[str, str]]]:
    return build_stints(changes(), universe.load_universe())


def membership(days: pd.DatetimeIndex, columns: list[str]) -> pd.DataFrame:
    """True where a company was really in the index on that day."""
    iso = np.array([d.strftime("%Y-%m-%d") for d in days])
    table = stints()
    arr = np.zeros((len(days), len(columns)), dtype=bool)
    for j, sym in enumerate(columns):
        for start, end in table.get(sym, []):
            arr[:, j] |= (iso >= start) & (iso < end)
    return pd.DataFrame(arr, index=days, columns=columns)


def _sectors(symbols: list[str], log=print) -> dict[str, str]:
    """Industry group for companies no longer in the index (looked up once, then remembered)."""
    path = CACHE / "pit_sectors.json"
    known = json.loads(path.read_text()) if path.exists() else {}
    todo = [s for s in symbols if s not in known]
    if todo:
        yf = yahoo(CACHE)
        log(f"looking up industry groups for {len(todo)} former members")
        for i, s in enumerate(todo):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    raw = yf.Ticker(s).info.get("sector") or "Other"
            except Exception:
                raw = "Other"
            known[s] = YAHOO_TO_GICS.get(raw, raw)
            time.sleep(0.25)
            if i % 40 == 39:
                path.write_text(json.dumps(known))
        path.write_text(json.dumps(known))
    return {s: known.get(s, "Other") for s in symbols}


def former_prices(former: list[str], log=print, now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """Price history of companies that used to be in the index, kept in its own files.

    Like the main history, a day's prices are only trusted if they were saved after that day's closing
    bell. Anything saved mid-session is re-downloaded (for companies still trading) or dropped."""
    paths = {f: CACHE / f"pit_{f}.parquet" for f in data.FIELDS}
    stamp = CACHE / "pit_meta.json"
    quiet = lambda m: None
    if not all(p.exists() for p in paths.values()):
        log(f"downloading history for {len(former)} former S&P 500 members (many will be gone from Yahoo)")
        got = data._clean(data._download(former, HISTORY_START, log=quiet))
        for f, df in got.items():
            df.to_parquet(paths[f])
        stamp.write_text(json.dumps({"saved_at": time.time()}))
        return got
    extra = {f: pd.read_parquet(p) for f, p in paths.items()}
    try:
        saved = float(json.loads(stamp.read_text())["saved_at"])
    except (OSError, ValueError, KeyError):
        saved = paths["close"].stat().st_mtime
    trusted = pd.Timestamp(mktcal.last_completed_session(datetime.fromtimestamp(saved, mktcal.NY)))
    want = pd.Timestamp(mktcal.last_completed_session(now))
    if trusted >= want:
        return extra
    extra = {f: df.loc[:trusted] for f, df in extra.items()}                 # later rows were saved before their bell: half days
    alive = sorted(extra["close"].columns[extra["close"].iloc[-10:].notna().any()])
    try:
        got = data._clean(data._download(alive, HISTORY_START, log=quiet)) if alive else {}
    except Exception as exc:
        log(f"could not refresh former members' prices ({type(exc).__name__}); using trusted days only")
        return extra
    for f in data.FIELDS:
        if f not in got:
            continue
        idx = extra[f].index.union(got[f].index)
        frame = extra[f].reindex(idx)
        for sym in got[f].columns:
            frame[sym] = got[f][sym].reindex(idx)                            # whole history again, so dividend adjustments stay consistent
        extra[f] = frame
    for f, df in extra.items():
        df.to_parquet(paths[f])
    stamp.write_text(json.dumps({"saved_at": (now.timestamp() if now else time.time())}))
    return extra


def pit_panel(log=print) -> tuple[dict[str, pd.DataFrame], dict[str, str], pd.DataFrame]:
    """Prices for today's members plus former members, their industry groups, and who was in when."""
    main = research.load_research_panel()
    former = sorted(s for s in stints() if s not in main["close"].columns)
    extra = {f: df.reindex(main["close"].index) for f, df in former_prices(former, log).items()}
    enough = extra["close"].columns[extra["close"].notna().sum() >= 252]
    panel = {f: pd.concat([main[f], extra[f][enough]], axis=1) for f in data.FIELDS}
    sectors = {**_sectors(list(enough), log), **universe.sector_map()}
    cols = F.tradable_columns(panel)
    return panel, sectors, membership(panel["close"].index, cols)


def recipe(mode: str | None = None, **changes) -> B.PortfolioParams:
    """The portfolio rules the live engine uses, for one of the three risk settings (the default one if not named)."""
    dial = RISK_MODES[mode or DEFAULT_MODE]
    return B.PortfolioParams(**{"max_positions": MAX_POSITIONS, "hold_buffer": HOLD_BUFFER, "target_vol": dial["target_vol"],
                                "trend_filter": dial["trend_filter"], **changes})


def targets(ds: F.Dataset) -> dict:
    """The answer sheet the forecaster learns from (config.TARGET). Grading always uses real returns."""
    if TARGET == "blend":
        return {"plain": ds.y, "risk": ds.y_risk}
    return {"risk": ds.y_risk, "sector": ds.y_sector, "both": ds.y_both}.get(TARGET, ds.y)


def dataset(spec: ModelSpec, log=print):
    """The fair training table: every company on the days it was really in the index."""
    panel, sectors, member = pit_panel(log)
    events = None
    if EVENT_FAMILIES:
        from . import events as EV
        events = EV.load()
    return panel, sectors, member, F.build_dataset(panel, sectors, horizons=spec.horizons, membership=member, risk_adjusted=True,
                                                   events=events, event_families=EVENT_FAMILIES or None)


def fit_production(spec: ModelSpec | None = None, log=print) -> Ensemble:
    """Train the live forecaster on everything known so far and save it."""
    spec = spec or ModelSpec()
    _, _, _, ds = dataset(spec, log)
    model = Ensemble(spec, ds.feature_names).fit(ds.X, targets(ds), None, ds.dates)
    model.meta.update({"trained_through": str(pd.Timestamp(ds.dates.max()).date()), "trained_at": datetime.now().isoformat(timespec="seconds"),
                       "learns": {"risk": "returns adjusted for how jumpy each stock is", "blend": "plain returns and risk-adjusted returns, averaged"}.get(TARGET, "plain returns"),
                       "target": TARGET,
                       "stock_list": "the S&P 500 as it really was each day", "companies": int(len(set(ds.symbols)))})
    model.save(MODELS / "production.json")
    return model


def run(seeds: int = 3, log=print) -> dict:
    spec = ModelSpec(seeds=tuple([11, 22, 33][:seeds]))
    panel, sectors, member, ds = dataset(spec, log)
    former = [c for c in member.columns if c not in set(universe.symbols())]
    per_day = pd.Series(1, index=ds.dates).groupby(level=0).sum()
    log(f"{panel['close'].shape[1]} symbols, {len(former)} of them former members; {len(ds.dates):,} rows; "
        f"members per day: {int(per_day.loc['2010':'2010'].median())} in 2010, {int(per_day.loc['2018':'2018'].median())} in 2018, {int(per_day.iloc[-1])} now")
    keep = RESEARCH / "round1"                                   # the round-one results, kept so before and after can be shown side by side
    if not keep.exists():
        keep.mkdir()
        for name in ("grade_pit_rehearsal.json", "grade_pit_exam.json", "pit.json", "safety_rails.json", "model_card.json"):
            if (RESEARCH / name).exists():
                (keep / name).write_bytes((RESEARCH / name).read_bytes())
    scores = research.walk_forward(ds, spec, 2010, None, log=log, targets=targets(ds))
    scores.to_parquet(RESEARCH / "scores_pit.parquet")
    mask = F.universe_mask(panel) & member
    etf = _real_funds()
    result = {"former_members_with_history": len(former), "seeds": seeds, "members_2010": int(per_day.loc["2010":"2010"].median()),
              "members_now": int(per_day.iloc[-1]), "periods": {}, "target": TARGET, "event_families": list(EVENT_FAMILIES),
              "signals": len(ds.feature_names), "default_mode": DEFAULT_MODE}
    blended = smooth_scores(scores, SCORE_SMOOTHING)
    modes: dict = {"default": DEFAULT_MODE, "dials": RISK_MODES}
    for label, lo, hi in (("rehearsal", "2010-01-01", "2024-12-31"), ("exam", "2025-01-01", None)):
        part = blended.loc[lo:hi] if hi else blended.loc[lo:]
        g = research.grade(part, panel, sectors, recipe(), label=f"fair {label}", mask=mask)
        rsp = B.metrics(etf["RSP"].reindex(part.index).dropna())
        g["real_equal_weight_fund"] = {"cagr": rsp["cagr"], "max_drawdown": rsp["max_drawdown"], "sharpe": rsp["sharpe"], "total_return": rsp["total_return"]}
        g["flattery_left"] = g["universe"]["cagr"] - rsp["cagr"]
        spy = panel["adj"]["SPY"].pct_change(fill_method=None).reindex(part.index).fillna(0.0)
        others = {}
        for name, prm in (("first_recipe_20_stocks", recipe(max_positions=20)), ("always_fully_invested", recipe(target_vol=None, trend_filter=False))):
            m = B.metrics(B.simulate(part, panel, prm, sectors).returns, spy)
            others[name] = {k: m[k] for k in ("cagr", "sharpe", "max_drawdown", "beta", "alpha")}
        g["other_recipes"] = others
        g["mode"] = DEFAULT_MODE
        for name in RISK_MODES:                                  # the same scores under each of the three risk settings
            res = B.simulate(part, panel, recipe(name), sectors)
            m = B.metrics(res.returns, spy)
            yearly = (1 + res.returns).groupby(res.returns.index.year).prod() - 1
            spy_y = (1 + spy).groupby(spy.index.year).prod() - 1
            row = {**{k: m[k] for k in ("cagr", "sharpe", "max_drawdown", "vol", "beta", "alpha", "total_return")},
                   "turnover": float(res.turnover.sum() / (len(part) / 252)), "avg_exposure": float(res.exposure.mean()),
                   "years_beat_spy": int((yearly > spy_y.reindex(yearly.index)).sum()), "years": int(len(yearly)),
                   "yearly": [{"year": int(y), "strategy": float(v), "spy": float(spy_y.get(y, np.nan))} for y, v in yearly.items()]}
            if label == "rehearsal":
                modes[name] = row
            else:
                modes[name]["exam"] = row
        sm = B.metrics(spy)
        modes["spy" if label == "rehearsal" else "exam_spy"] = {k: sm[k] for k in ("cagr", "sharpe", "max_drawdown", "vol", "total_return")}
        modes["period" if label == "rehearsal" else "exam_period"] = [g["start"], g["end"]]
        luck = research.placebo(scores, panel, sectors, start=lo, runs=30, log=lambda m: None, end=hi, params=recipe())
        g["luck"] = {k: luck[k] for k in ("actual", "runs", "shuffled_low", "shuffled_mid", "shuffled_high", "beaten_cagr", "beaten_sharpe")}
        (RESEARCH / f"grade_pit_{label}.json").write_text(json.dumps(research._clean(g)))
        result["periods"][label] = {"start": g["start"], "end": g["end"], "strategy": g["strategy"]["cagr"], "spy": g["spy"]["cagr"],
                                    "universe": g["universe"]["cagr"], "rsp": rsp["cagr"]}
        log(research.summary_line(g) + f"  | real equal-weight fund {rsp['cagr']:+.1%}, flattery left {g['flattery_left'] * 100:+.1f} pts"
            f" | 20-stock recipe {others['first_recipe_20_stocks']['cagr']:+.1%} | fully invested {others['always_fully_invested']['cagr']:+.1%}"
            f" | luck test: beat {luck['beaten_cagr']} of {luck['runs']} (shuffled {luck['shuffled_low']:+.1%}..{luck['shuffled_high']:+.1%})")
    (RESEARCH / "pit.json").write_text(json.dumps(research._clean(result)))
    (RESEARCH / "modes.json").write_text(json.dumps(research._clean(modes)))
    for name in RISK_MODES:
        a, x = modes[name], modes[name]["exam"]
        log(f"  {name:<9} 2010-24 {a['cagr']:+.1%} Sh {a['sharpe']:.2f} DD {a['max_drawdown']:.0%} beat S&P {a['years_beat_spy']}/{a['years']} yrs | exam {x['cagr']:+.1%} Sh {x['sharpe']:.2f} DD {x['max_drawdown']:.0%}")
    model = fit_production(spec, log)
    from . import explain
    (RESEARCH / "model_card.json").write_text(json.dumps(research._clean(explain.model_card(model))))
    log(f"saved production forecaster: {model.meta}")
    return result


def safety(log=print) -> dict:
    """Test the live safety rules (stop-loss, loss limits) against history on the fair list."""
    from dataclasses import replace
    panel, sectors, _ = pit_panel(lambda m: None)
    scores = smooth_scores(pd.read_parquet(RESEARCH / "scores_pit.parquet"), SCORE_SMOOTHING)
    out = {}
    for label, lo, hi in (("rehearsal", "2010-01-01", "2024-12-31"), ("exam", "2025-01-01", None)):
        part = scores.loc[lo:hi] if hi else scores.loc[lo:]
        rows, base_returns = [], None
        for stop in (None, 0.08, 0.12, 0.20):
            res = B.simulate(part, panel, replace(recipe(), position_stop=stop), sectors)
            m = B.metrics(res.returns)
            rows.append({"stop": stop, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"]})
            if stop is None:
                base_returns = res.returns
        eq = (1 + base_returns).cumprod()
        dd = eq / np.maximum(1.0, eq.cummax()) - 1
        falls = lambda level: int(((dd <= -level) & ~(dd <= -level).shift(1, fill_value=False)).sum())
        out[label] = {"stops": rows, "days": int(len(base_returns)), "days_down_3pct": int((base_returns <= -0.03).sum()),
                      "drops_15": falls(0.15), "drops_20": falls(0.20), "drops_25": falls(0.25), "worst_day": float(base_returns.min()),
                      "worst_drop": float(dd.min())}
        log(f"{label}: " + ", ".join(f"stop {r['stop']}: {r['cagr']:+.1%}/{r['max_drawdown']:.0%}" for r in rows) + f" | 3% down days {out[label]['days_down_3pct']}, falls of 15/20/25%: {out[label]['drops_15']}/{out[label]['drops_20']}/{out[label]['drops_25']}")
    (RESEARCH / "safety_rails.json").write_text(json.dumps(research._clean(out)))
    return out


def _real_funds() -> pd.DataFrame:
    """Daily returns of real funds, the yardsticks that need no correction."""
    path = CACHE / "real_funds.parquet"
    if not path.exists() or time.time() - path.stat().st_mtime > 86400:
        yf = yahoo(CACHE)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            px = yf.download(["RSP", "MDY", "SPY"], start=HISTORY_START, interval="1d", auto_adjust=False, progress=False)["Adj Close"]
        px.index = pd.DatetimeIndex(px.index).tz_localize(None).normalize()
        px.pct_change(fill_method=None).to_parquet(path)
    return pd.read_parquet(path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "safety":
        safety(lambda m: print(m, flush=True))
    else:
        run(int(sys.argv[1]) if len(sys.argv) > 1 else 3, lambda m: print(m, flush=True))
