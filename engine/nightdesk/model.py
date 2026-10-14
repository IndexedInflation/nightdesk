"""The forecaster: a committee of gradient-boosted decision trees plus one straight-line model.

Each member looks at a stock's ~65 ranked signals and guesses how the stock will rank against
the others over the next few days. Their guesses are averaged. The output is a score per
stock: higher means "expected to do better than the rest", not a price target.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge


@dataclass
class ModelSpec:
    horizons: tuple[int, ...] = (5, 21)       # forecast the next week and the next month
    seeds: tuple[int, ...] = (11, 22, 33)     # three tree models per horizon, each sees a different random half
    n_trees: int = 300
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_leaf: int = 2000                      # a rule must hold for 2,000+ examples before it is believed
    feature_fraction: float = 0.6
    bagging_fraction: float = 0.5
    l2: float = 10.0
    ridge_alpha: float = 1.0
    ridge_weight: float = 0.3                 # 30% straight-line model, 70% trees
    threads: int = 8
    objective: str = "regression"             # "regression" (guess the rank) or "rank" (learn the ordering directly)
    decay_years: float | None = None          # if set, an example this many years old counts half as much

    def lgb_params(self, seed: int) -> dict:
        return dict(objective="rank_xendcg" if self.objective == "rank" else "regression", learning_rate=self.learning_rate, num_leaves=self.num_leaves,
                    min_data_in_leaf=self.min_leaf, feature_fraction=self.feature_fraction,
                    bagging_fraction=self.bagging_fraction, bagging_freq=1, lambda_l2=self.l2,
                    max_bin=63, num_threads=self.threads, seed=seed, verbose=-1,
                    force_col_wise=True, deterministic=True)


def zscore_by_group(values: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Standardise within each group (each day): mean 0, spread 1."""
    _, inv = np.unique(groups, return_inverse=True)
    n = np.bincount(inv).astype("float64")
    mean = np.bincount(inv, weights=values) / n
    centred = values - mean[inv]
    sd = np.sqrt(np.bincount(inv, weights=centred ** 2) / np.maximum(n - 1, 1))
    sd[sd < 1e-12] = 1.0
    return centred / sd[inv]


def _linear(X: np.ndarray, coef: np.ndarray, intercept: float, chunk: int = 200_000) -> np.ndarray:
    """weights . signals + offset, in pieces so a big table never needs a second copy in memory."""
    out = np.empty(len(X))
    for i in range(0, len(X), chunk):
        out[i:i + chunk] = np.dot(X[i:i + chunk].astype("float64"), coef) + intercept
    return out


class Ensemble:
    def __init__(self, spec: ModelSpec, feature_names: list[str]):
        self.spec = spec
        self.feature_names = list(feature_names)
        self.trees: dict = {}                                   # head -> tree models. A head is a horizon (5), or "sheet:horizon" ("risk:5")
        self.lines: dict = {}                                   # head -> (weights, offset)
        self.meta: dict = {}

    @property
    def members(self) -> int:
        """How many models vote: every tree model and every straight-line model."""
        return sum(len(b) for b in self.trees.values()) + len(self.lines)

    def fit(self, X: np.ndarray, y_by_h: dict[int, np.ndarray], row_ok: dict[int, np.ndarray] | None = None,
            dates: np.ndarray | None = None) -> "Ensemble":
        """Train every committee member. `row_ok[h]` marks rows usable for horizon h.
        `dates` (the day of each row, in order) is needed for the ranking objective and recency weights.

        `y_by_h` is one answer sheet, {horizon: answers}, or several, {"plain": {...}, "risk": {...}}.
        With several, a full set of models is trained on each and they all vote with equal weight."""
        nested = isinstance(next(iter(y_by_h.values())), dict)
        sheets = y_by_h if nested else {None: y_by_h}
        for sheet, h in [(sh, h) for sh in sheets for h in self.spec.horizons]:
            self._fit_head(h if sheet is None else f"{sheet}:{h}", X, sheets[sheet][h], row_ok[h] if row_ok is not None else None, dates)
            if sheet is None or sheet == next(iter(sheets)):
                self.meta[f"rows_h{h}"] = int((~np.isnan(sheets[sheet][h]) & (row_ok[h] if row_ok is not None else True)).sum())
        if nested:
            self.meta["answer_sheets"] = list(sheets)
        return self

    def _fit_head(self, h, X: np.ndarray, y: np.ndarray, usable: np.ndarray | None, dates: np.ndarray | None) -> None:
        """Train the tree models and the straight-line model for one head (one answer sheet at one horizon)."""
        import lightgbm as lgb
        ok = ~np.isnan(y)
        if usable is not None:
            ok &= usable
        Xh, yh = X[ok], y[ok]
        label, group, weight = yh, None, None
        if self.spec.objective == "rank":             # grade each stock 0-15 within its day; a "group" is one day
            label = np.clip(((yh + 0.5) * 16).astype(np.int32), 0, 15)
            group = np.unique(dates[ok], return_counts=True)[1]
        if self.spec.decay_years:
            age = (dates[ok].max() - dates[ok]).astype("timedelta64[D]").astype(np.float64) / 365.25
            weight = 0.5 ** (age / self.spec.decay_years)
        self.trees[h] = []
        for seed in self.spec.seeds:
            ds = lgb.Dataset(Xh, label=label, group=group, weight=weight, feature_name=self.feature_names, free_raw_data=False)
            self.trees[h].append(lgb.train(self.spec.lgb_params(seed), ds, num_boost_round=self.spec.n_trees))
        with np.errstate(all="ignore"):               # Apple's maths library raises false alarms here
            line = Ridge(alpha=self.spec.ridge_alpha).fit(Xh, yh)
        coef = line.coef_.astype("float64")
        if not np.isfinite(coef).all():
            raise FloatingPointError("straight-line model produced non-finite weights")
        self.lines[h] = (coef, float(line.intercept_))


    def score(self, X: np.ndarray, groups: np.ndarray | None = None) -> np.ndarray:
        """Blended score per row. `groups` = the day of each row (one day if omitted)."""
        if groups is None:
            groups = np.zeros(len(X), dtype="int64")
        total = np.zeros(len(X))
        rw = self.spec.ridge_weight
        for h in self.trees:                                     # every head votes with equal weight
            tree = np.mean([b.predict(X, num_threads=self.spec.threads) for b in self.trees[h]], axis=0)
            line = _linear(X, *self.lines[h])
            total += (1 - rw) * zscore_by_group(tree, groups) + rw * zscore_by_group(line, groups)
        return total / len(self.trees)

    def importance(self) -> dict[str, float]:
        """Share of the trees' total learning ("gain") owed to each signal."""
        gain = np.zeros(len(self.feature_names))
        for boosters in self.trees.values():
            for b in boosters:
                gain += b.feature_importance(importance_type="gain")
        gain = gain / gain.sum() if gain.sum() > 0 else gain
        return dict(sorted(zip(self.feature_names, gain.tolist()), key=lambda kv: -kv[1]))

    def save(self, path: Path) -> None:
        blob = {
            "spec": asdict(self.spec), "feature_names": self.feature_names, "meta": self.meta,
            "trees": {h: [b.model_to_string() for b in bs] for h, bs in self.trees.items()},
            "lines": {str(h): [coef.tolist(), b] for h, (coef, b) in self.lines.items()},
            "format": "nightdesk-ensemble-v1",
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(blob, allow_nan=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Ensemble":
        # Declarative JSON cannot execute Python code; legacy pickle files are never loaded.
        import lightgbm as lgb
        blob = json.loads(path.read_text(encoding="utf-8"))
        if blob.get("format") != "nightdesk-ensemble-v1":
            raise ValueError("Unsupported model format. Train a new forecaster in Setup.")
        spec = ModelSpec(**{**blob["spec"], "horizons": tuple(blob["spec"]["horizons"]), "seeds": tuple(blob["spec"]["seeds"])})
        ens = cls(spec, blob["feature_names"])
        ens.meta = blob.get("meta", {})
        head = lambda h: h if isinstance(h, str) and ":" in h else int(h)
        ens.trees = {head(h): [lgb.Booster(model_str=s) for s in strs] for h, strs in blob["trees"].items()}
        ens.lines = {head(h): (np.asarray(coef, dtype="float64"), float(b)) for h, (coef, b) in blob["lines"].items()}
        return ens
