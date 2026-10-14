"""Plain-English reasons. Turns "signal 37 pushed the score up" into words a person can read."""
from __future__ import annotations

import numpy as np

# signal name -> (what a HIGH reading means, what a LOW reading means)
PHRASES: dict[str, tuple[str, str]] = {
    "r1": ("jumped today", "fell today"), "r2": ("up over the last two days", "down over the last two days"),
    "r3": ("up over the last three days", "down over the last three days"), "r5": ("up this week", "down this week"),
    "r10": ("up over two weeks", "down over two weeks"), "r21": ("up this month", "down this month"),
    "r63": ("strong last 3 months", "weak last 3 months"), "r126": ("strong last 6 months", "weak last 6 months"),
    "r252": ("strong last 12 months", "weak last 12 months"),
    "mom_12_1": ("strong run over the past year", "poor run over the past year"),
    "mom_6_1": ("strong run over the past half-year", "poor past half-year"),
    "mom_3m_1w": ("steady climb over 3 months", "steady slide over 3 months"),
    "seas_12m": ("did well this time last year", "did badly this time last year"),
    "on_1": ("gapped up overnight", "gapped down overnight"), "id_1": ("rose during today's session", "fell during today's session"),
    "on_5": ("gaining overnight this week", "losing overnight this week"), "on_21": ("gaining overnight this month", "losing overnight this month"),
    "id_5": ("gaining during the day this week", "fading during the day this week"),
    "id_21": ("gaining during the day this month", "fading during the day this month"),
    "vol_5": ("jumpy price this week", "calm price this week"), "vol_21": ("jumpy price this month", "calm price this month"),
    "vol_63": ("jumpy price", "calm price"), "vol_ratio": ("getting jumpier", "calming down"),
    "downvol_21": ("sharp down-days lately", "few sharp down-days"),
    "range_5": ("wide daily swings this week", "narrow daily swings this week"), "range_21": ("wide daily swings", "narrow daily swings"),
    "atr_14": ("wide daily swings", "narrow daily swings"),
    "max_21": ("had a big up-day recently", "no big up-days recently"), "min_21": ("no big down-days recently", "had a big down-day recently"),
    "skew_63": ("its big moves have been up", "its big moves have been down"),
    "beta_126": ("moves more than the market", "moves less than the market"),
    "ivol_63": ("moves a lot on its own news", "quiet on its own news"),
    "resid_5": ("beating the market this week", "lagging the market this week"),
    "resid_21": ("beating the market this month", "lagging the market this month"),
    "corr_63": ("moves closely with the market", "moves independently of the market"),
    "hi_252": ("near its 1-year high", "far below its 1-year high"), "lo_252": ("far above its 1-year low", "near its 1-year low"),
    "hi_21": ("at its high for the month", "well off its month high"), "pos_21": ("top of its monthly range", "bottom of its monthly range"),
    "clv_1": ("near the day's high", "near the day's low"), "clv_5": ("closing near daily highs", "closing near daily lows"),
    "ma_5": ("above its 1-week average", "below its 1-week average"), "ma_10": ("above its 2-week average", "below its 2-week average"),
    "ma_21": ("above its 1-month average", "below its 1-month average"), "ma_50": ("above its 50-day average", "below its 50-day average"),
    "ma_200": ("above its 200-day average", "below its 200-day average"), "ma_50_200": ("in a long-term uptrend", "in a long-term downtrend"),
    "rsi_2": ("stretched upward short-term", "stretched downward short-term"), "rsi_14": ("overbought on the usual gauge", "oversold on the usual gauge"),
    "bb_21": ("above its normal monthly band", "below its normal monthly band"),
    "relvol_1": ("unusually heavy trading today", "unusually light trading today"),
    "relvol_5": ("busier than normal this week", "quieter than normal this week"),
    "vol_trend": ("trading interest rising", "trading interest fading"),
    "dvol_63": ("among the most heavily traded", "among the less traded"),
    "amihud_21": ("price moves easily", "price hard to move"),
    "upvol_21": ("more volume on up-days", "more volume on down-days"), "pv_corr_21": ("rises on heavy volume", "falls on heavy volume"),
    "shock": ("big up-move on big volume", "big down-move on big volume"),
    "sec_rel_5": ("beating its industry this week", "lagging its industry this week"),
    "sec_rel_21": ("beating its industry this month", "lagging its industry this month"),
    "sec_rel_63": ("beating its industry over 3 months", "lagging its industry over 3 months"),
    "sec_mom_21": ("its industry is strong this month", "its industry is weak this month"),
    "sec_mom_126": ("its industry is strong this half-year", "its industry is weak this half-year"),
    "jump_age": ("no big news move recently", "big news move recently"), "jump_ret": ("last big news move was up", "last big news move was down"),
}


def phrase(feature: str, ranked_value: float) -> str:
    high, low = PHRASES.get(feature, (feature + " high", feature + " low"))
    return high if ranked_value >= 0 else low


def contributions(model, X: np.ndarray) -> np.ndarray:
    """How much each signal pushed each stock's tree score up or down (rows x signals)."""
    total = np.zeros((len(X), len(model.feature_names)))
    for boosters in model.trees.values():
        for b in boosters:
            total += b.predict(X, pred_contrib=True)[:, :-1]
    return total


def reasons(model, X: np.ndarray, top: int = 3) -> list[dict]:
    """For each row: the signals that helped most and hurt most, in words."""
    contrib = contributions(model, X)
    out = []
    for i in range(len(X)):
        order = np.argsort(contrib[i])
        ups = [model.feature_names[j] for j in order[::-1][:top] if contrib[i, j] > 0]
        downs = [model.feature_names[j] for j in order[:2] if contrib[i, j] < 0]
        dedup = lambda names: list(dict.fromkeys(phrase(n, X[i, model.feature_names.index(n)]) for n in names))
        out.append({"for": dedup(ups), "against": dedup(downs)})
    return out


# ---- neutral names and families, for the "which measurements matter" chart ----
FAMILIES: dict[str, list[str]] = {
    "Momentum (months)": ["r21", "r63", "r126", "r252", "mom_12_1", "mom_6_1", "mom_3m_1w", "seas_12m"],
    "Snap-back (days)": ["r1", "r2", "r3", "r5", "r10", "on_1", "id_1", "on_5", "on_21", "id_5", "id_21", "rsi_2", "rsi_14", "bb_21", "clv_1", "clv_5"],
    "Jumpiness": ["vol_5", "vol_21", "vol_63", "vol_ratio", "downvol_21", "range_5", "range_21", "atr_14", "max_21", "min_21", "skew_63", "ivol_63"],
    "Link to the market": ["beta_126", "corr_63", "resid_5", "resid_21"],
    "Position vs its own past": ["hi_252", "lo_252", "hi_21", "pos_21", "ma_5", "ma_10", "ma_21", "ma_50", "ma_200", "ma_50_200"],
    "Trading activity": ["relvol_1", "relvol_5", "vol_trend", "dvol_63", "amihud_21", "upvol_21", "pv_corr_21", "shock"],
    "Versus its industry": ["sec_rel_5", "sec_rel_21", "sec_rel_63", "sec_mom_21", "sec_mom_126"],
    "Big-news days": ["jump_age", "jump_ret"],
}
LABELS: dict[str, str] = {
    "r1": "Move over the last day", "r2": "Move over the last 2 days", "r3": "Move over the last 3 days", "r5": "Move over the last week",
    "r10": "Move over the last 2 weeks", "r21": "Move over the last month", "r63": "Move over the last 3 months", "r126": "Move over the last 6 months",
    "r252": "Move over the last 12 months", "mom_12_1": "12-month run, skipping the latest month", "mom_6_1": "6-month run, skipping the latest month",
    "mom_3m_1w": "3-month run, skipping the latest week", "seas_12m": "What it did this month a year ago",
    "on_1": "Overnight gap today", "id_1": "Move during today's session", "on_5": "Overnight gaps this week", "on_21": "Overnight gaps this month",
    "id_5": "Daytime moves this week", "id_21": "Daytime moves this month",
    "vol_5": "Jumpiness this week", "vol_21": "Jumpiness this month", "vol_63": "Jumpiness over 3 months", "vol_ratio": "Jumpiness now vs normal",
    "downvol_21": "Size of recent down-days", "range_5": "Daily high-to-low range this week", "range_21": "Daily high-to-low range this month",
    "atr_14": "Average daily range (2 weeks)", "max_21": "Biggest up-day this month", "min_21": "Biggest down-day this month",
    "skew_63": "Lopsidedness of its big moves", "beta_126": "How much it moves with the market", "ivol_63": "Jumpiness on its own news",
    "resid_5": "Move this week beyond the market's", "resid_21": "Move this month beyond the market's", "corr_63": "How closely it tracks the market",
    "hi_252": "Distance below its 1-year high", "lo_252": "Distance above its 1-year low", "hi_21": "Distance below its 1-month high",
    "pos_21": "Place within its 1-month range", "clv_1": "Where today's price sits in the day's range", "clv_5": "Where it has been closing in the day's range",
    "ma_5": "Price vs 1-week average", "ma_10": "Price vs 2-week average", "ma_21": "Price vs 1-month average", "ma_50": "Price vs 50-day average",
    "ma_200": "Price vs 200-day average", "ma_50_200": "50-day average vs 200-day average",
    "rsi_2": "Short-term stretch gauge (2-day RSI)", "rsi_14": "Standard stretch gauge (14-day RSI)", "bb_21": "Distance outside its normal monthly band",
    "relvol_1": "Today's volume vs normal", "relvol_5": "This week's volume vs normal", "vol_trend": "Volume trend", "dvol_63": "Dollars traded per day",
    "amihud_21": "How easily the price moves", "upvol_21": "Share of volume on up-days", "pv_corr_21": "Whether it rises or falls on heavy volume",
    "shock": "Today's move times today's volume", "sec_rel_5": "Move this week vs its industry", "sec_rel_21": "Move this month vs its industry",
    "sec_rel_63": "Move over 3 months vs its industry", "sec_mom_21": "Its industry's move this month", "sec_mom_126": "Its industry's move over 6 months",
    "jump_age": "Days since a big-news move", "jump_ret": "Direction of the last big-news move",
}
FAMILY_OF = {f: fam for fam, feats in FAMILIES.items() for f in feats}


def model_card(model) -> dict:
    """What the forecaster leans on, for the app and the explainer page."""
    imp = model.importance()
    fam: dict[str, float] = {}
    for f, share in imp.items():
        fam[FAMILY_OF.get(f, "Other")] = fam.get(FAMILY_OF.get(f, "Other"), 0.0) + share
    return {
        "meta": model.meta, "signals": len(model.feature_names), "members": sum(len(b) for b in model.trees.values()) + len(model.lines),
        "top": [{"feature": f, "label": LABELS.get(f, f), "family": FAMILY_OF.get(f, "Other"), "share": round(s, 5)} for f, s in list(imp.items())[:20]],
        "families": [{"name": k, "share": round(v, 5)} for k, v in sorted(fam.items(), key=lambda kv: -kv[1])],
    }
