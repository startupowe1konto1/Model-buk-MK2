"""Pi-ratings + gradient boosting: one pooled cross-league booster per market.

The evaluation protocol refits a model per league before every matchday, but a
booster fitted on one league (a few thousand matches) cannot learn.  So the
per-league :class:`PiGbmModel` is a thin wrapper: it takes the pre-kickoff
features of the matchday from the shared as-of store
(:class:`~model_buk.models.match_features.AsOfStore`) and reads the probability
mapping from a booster fitted on the *pooled* matches of all leagues.

What is recomputed at every matchday and what is cached
--------------------------------------------------------
- Features (pi-ratings, rolling form, rest days, league level, ...) are computed
  at every matchday from the matches that kicked off before that matchday's
  training cut-off ``C = first kickoff of the matchday - result latency`` --
  exactly the cut-off of the framework's own training set.
- The booster (the mapping features -> probabilities) is fitted on pooled
  matches that kicked off before ``B = floor(C / 28 days) * 28 days``.  ``B`` is
  never later than ``C``, so the mapping only ever saw strictly earlier matches
  (features of a training match are as-of its own kickoff minus the latency,
  its label is its own result).  One mapping serves every league and every
  matchday between two multiples of 28 days and is cached (in memory and on
  disk), which turns ~6,800 x (1 + grid) refits into one fit per
  (market, hyper-parameters, 28-day bucket).  The mapping is therefore at most
  28 days older than a per-matchday refit would be.
- The hyper-parameter grid (learning rate x depth) is chosen by the framework
  on the season before each test season, like for every other model.

Nothing from a match at or after ``C`` reaches a forecast: features come from
the store with the cut-off, training rows are ``t < B <= C``, and
:meth:`PiGbmModel.predict` refuses a training frame that reaches past ``C``.
Injuries are not used (they are retrospective).

The store and the boosters live in a :class:`PiGbmEngine`; the protocol spec is
built with :func:`pi_gbm_spec`.  :func:`pi_logit_spec` is the same machinery with a small
pooled logit on the ratings and the league level instead of a booster (an ablation: what the
ratings alone are worth), saved as model "pi_ratings".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from model_buk.evaluation.framework import Market, ModelSpec
from model_buk.models.match_features import AsOfStore, TrainingMatrix
from model_buk.models.pi_ratings import PiRatingConfig

_NS_PER_HOUR = 3_600_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR
_PROBABILITY_FLOOR = 1e-6

DEFAULT_GRID: dict[str, list[Any]] = {"learning_rate": [0.08], "depth": [4, 6]}


@dataclass(frozen=True)
class GbmSettings:
    """Fixed booster settings (the grid varies only learning rate and depth)."""

    iterations: int = 1000
    l2_leaf_reg: float = 5.0
    border_count: int = 64
    early_stopping_rounds: int = 40
    validation_share: float = 0.08  # most recent share of the training rows, used only to stop boosting
    min_train_rows: int = 3000
    bucket_days: int = 28
    thread_count: int = 1
    seed: int = 0

    def __post_init__(self) -> None:
        if self.bucket_days < 1:
            raise ValueError("bucket_days must be positive")
        if not 0.0 < self.validation_share < 0.5:
            raise ValueError("validation_share must lie in (0, 0.5)")
        if self.min_train_rows < 10:
            raise ValueError("min_train_rows must be at least 10")


def params_key(params: Mapping[str, Any]) -> str:
    """Stable short text of a hyper-parameter dict (file names, cache keys)."""
    return json.dumps(dict(params), sort_keys=True, default=str)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def fit_booster(X: np.ndarray, y: np.ndarray, n_classes: int, params: Mapping[str, Any], settings: GbmSettings) -> Any:
    """CatBoost classifier on time-ordered rows; the last rows serve only to stop boosting early."""
    from catboost import CatBoostClassifier

    n = len(y)
    n_val = max(int(n * settings.validation_share), 1)
    model = CatBoostClassifier(
        loss_function="MultiClass" if n_classes > 2 else "Logloss",
        iterations=settings.iterations,
        learning_rate=float(params["learning_rate"]),
        depth=int(params["depth"]),
        l2_leaf_reg=settings.l2_leaf_reg,
        border_count=settings.border_count,
        od_type="Iter",
        od_wait=settings.early_stopping_rounds,
        use_best_model=True,
        thread_count=settings.thread_count,
        random_seed=settings.seed,
        verbose=0,
        allow_writing_files=False,
    )
    model.fit(X[: n - n_val], y[: n - n_val], eval_set=(X[n - n_val :], y[n - n_val :]))
    return model


def _fit_task(matrix_dir: str, market_name: str, params: dict[str, Any], settings: dict[str, Any], cut_ns: int, out_path: str) -> dict[str, Any]:
    """Worker entry point: fit one (market, params, cut) booster from the on-disk matrix and save it."""
    started = time.perf_counter()
    root = Path(matrix_dir)
    t = np.load(root / "t_ns.npy", mmap_mode="r")
    rows = int(np.searchsorted(t, cut_ns, side="left"))  # matrix is sorted by kickoff: rows with t < cut
    X = np.asarray(np.load(root / "X.npy", mmap_mode="r")[:rows])
    y = np.asarray(np.load(root / f"y_{_slug(market_name)}.npy", mmap_mode="r")[:rows])
    cfg = GbmSettings(**settings)
    n_classes = int(y.max()) + 1 if len(y) else 2
    model = fit_booster(X, y, max(n_classes, 2), params, cfg)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path + ".tmp"
    model.save_model(tmp, format="cbm")
    os.replace(tmp, out_path)
    return {
        "market": market_name,
        "params": params_key(params),
        "cut_ns": int(cut_ns),
        "rows": rows,
        "best_iteration": int(model.get_best_iteration() or 0),
        "seconds": time.perf_counter() - started,
    }


class GbmBank:
    """Pooled boosters keyed by (market, hyper-parameters, 28-day bucket), cached in memory and on disk."""

    def __init__(
        self,
        matrix: TrainingMatrix,
        settings: GbmSettings,
        *,
        latency_ns: int,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.matrix = matrix
        self.settings = settings
        self.latency_ns = int(latency_ns)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._bucket_ns = settings.bucket_days * _NS_PER_DAY
        self._models: dict[tuple[str, str, int], Any] = {}
        self._labels: dict[str, np.ndarray] = {}
        self._fingerprint: dict[int, str] = {}

    # -- keys

    def cut_for(self, cutoff_ns: int) -> int:
        """Exclusive kickoff bound of the training rows of the booster used at ``cutoff_ns`` (never after it)."""
        return int(cutoff_ns // self._bucket_ns) * self._bucket_ns

    def n_rows_before(self, cut_ns: int) -> int:
        return int(np.searchsorted(self.matrix.t_ns, cut_ns, side="left"))

    def labels(self, market: Market) -> np.ndarray:
        if market.name not in self._labels:
            goals = pd.DataFrame({"home_goals": self.matrix.home_goals, "away_goals": self.matrix.away_goals})
            outcome = market.outcome(goals)
            if np.isnan(outcome).any():
                raise ValueError("training rows must have a known outcome")
            self._labels[market.name] = outcome.astype(np.int64)
        return self._labels[market.name]

    def _rows_fingerprint(self, rows: int) -> str:
        if rows not in self._fingerprint:
            m = self.matrix
            digest = hashlib.sha1()
            digest.update(json.dumps([list(m.names), asdict(self.settings), self.latency_ns, rows]).encode())
            digest.update(np.ascontiguousarray(m.fixture_id[:rows]).tobytes())
            digest.update(np.ascontiguousarray(m.home_goals[:rows]).tobytes())
            digest.update(np.ascontiguousarray(m.away_goals[:rows]).tobytes())
            digest.update(np.ascontiguousarray(m.X[: min(rows, 2000)]).tobytes())
            self._fingerprint[rows] = digest.hexdigest()[:16]
        return self._fingerprint[rows]

    def path(self, market: Market, params: Mapping[str, Any], cut_ns: int) -> Path | None:
        if self.cache_dir is None:
            return None
        rows = self.n_rows_before(cut_ns)
        name = f"{_slug(market.name)}__{_slug(params_key(params))}__{cut_ns // _NS_PER_DAY}d__{self._rows_fingerprint(rows)}.cbm"
        return self.cache_dir / name

    # -- access

    def usable(self, cut_ns: int) -> bool:
        return self.n_rows_before(cut_ns) >= self.settings.min_train_rows

    def model(self, market: Market, params: Mapping[str, Any], cut_ns: int) -> Any | None:
        """The booster for this bucket, or ``None`` when fewer than ``min_train_rows`` matches precede it."""
        key = (market.name, params_key(params), int(cut_ns))
        if key in self._models:
            return self._models[key]
        if not self.usable(cut_ns):
            self._models[key] = None
            return None
        from catboost import CatBoostClassifier

        path = self.path(market, params, cut_ns)
        if path is not None and path.exists():
            loaded = CatBoostClassifier()
            loaded.load_model(str(path))
            self._models[key] = loaded
            return loaded
        rows = self.n_rows_before(cut_ns)
        fitted = fit_booster(self.matrix.X[:rows], self.labels(market)[:rows], len(market.classes), params, self.settings)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            fitted.save_model(tmp, format="cbm")
            os.replace(tmp, path)
        self._models[key] = fitted
        return fitted

    # -- parallel prefetch

    def write_matrix_files(self, markets: Sequence[Market]) -> Path:
        """Dump the training matrix and labels next to the boosters so worker processes can memory-map them."""
        if self.cache_dir is None:
            raise ValueError("a cache_dir is needed to share the training matrix with workers")
        rows = len(self.matrix)
        root = self.cache_dir / f"matrix_{self._rows_fingerprint(rows)}"
        root.mkdir(parents=True, exist_ok=True)

        def store(name: str, array: np.ndarray) -> None:
            # The directory name only fingerprints part of the matrix, so a file left by an earlier run is
            # trusted only if it equals the matrix in memory; otherwise the workers would fit on stale features.
            target = root / name
            if target.exists():
                on_disk = np.load(target, mmap_mode="r")
                if on_disk.shape == array.shape and on_disk.dtype == array.dtype and np.array_equal(on_disk, array, equal_nan=True):
                    return
                del on_disk
            np.save(root / (name + ".tmp.npy"), array)
            os.replace(root / (name + ".tmp.npy"), target)

        store("X.npy", self.matrix.X)
        store("t_ns.npy", self.matrix.t_ns)
        for market in markets:
            store(f"y_{_slug(market.name)}.npy", self.labels(market))
        return root

    def missing_tasks(self, markets: Sequence[Market], combos: Sequence[Mapping[str, Any]], cuts: Sequence[int]) -> list[tuple[Market, dict[str, Any], int, Path]]:
        """(market, params, cut, path) for every booster that is needed, usable and not on disk yet."""
        tasks = []
        for market in markets:
            for params in combos:
                for cut in sorted({int(c) for c in cuts}):
                    if not self.usable(cut):
                        continue
                    path = self.path(market, params, cut)
                    if path is not None and not path.exists():
                        tasks.append((market, dict(params), cut, path))
        return tasks

    def prefetch(self, markets: Sequence[Market], combos: Sequence[Mapping[str, Any]], cuts: Sequence[int], *, n_jobs: int = 3, log=print) -> None:
        """Fit every missing (market, params, cut) booster with ``n_jobs`` worker processes (resumable)."""
        from joblib import Parallel, delayed

        tasks = self.missing_tasks(markets, combos, cuts)
        if not tasks:
            return
        root = self.write_matrix_files(markets)
        settings = asdict(self.settings)
        started = time.perf_counter()
        log(f"prefetch: {len(tasks)} boosters to fit with {n_jobs} workers")
        runner = Parallel(n_jobs=n_jobs, backend="loky", return_as="generator_unordered")
        done = 0
        for info in runner(
            delayed(_fit_task)(str(root), market.name, params, settings, cut, str(path)) for market, params, cut, path in tasks
        ):
            done += 1
            if done % 10 == 0 or done == len(tasks):
                log(
                    f"  fitted {done}/{len(tasks)} in {time.perf_counter() - started:.0f}s "
                    f"(last: {info['market']} {info['params']} rows={info['rows']} iters={info['best_iteration']} {info['seconds']:.1f}s)"
                )


LOGIT_FEATURES: tuple[str, ...] = (
    "pi_exp_gd",
    "pi_h_home",
    "pi_h_away",
    "pi_a_home",
    "pi_a_away",
    "lg_home_goals",
    "lg_away_goals",
    "lg_home_win",
    "lg_draw",
)


class PooledLogit:
    """Multinomial logit on the pi-ratings and the league level (a ratings-only reference for the boosters).

    Features are the ratings, the goal difference they imply (and its absolute value, which carries the
    "unbalanced match" signal for totals) and the league level.  A row with a missing feature gets a NaN
    forecast, never an imputed one.  Softmax with two classes is the ordinary logit.
    """

    def __init__(self, names: Sequence[str], columns: np.ndarray, mean: np.ndarray, scale: np.ndarray, weights: np.ndarray) -> None:
        self.names = tuple(names)
        self.columns = columns
        self.mean = mean
        self.scale = scale
        self.weights = weights  # (1 + d, K): intercept first

    @staticmethod
    def _design(X: np.ndarray, columns: np.ndarray) -> np.ndarray:
        base = np.asarray(X, dtype=float)[:, columns]
        return np.column_stack([base, np.abs(base[:, 0])])  # column 0 is pi_exp_gd

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, n_classes: int, names: Sequence[str], l2: float = 1e-3) -> "PooledLogit":
        columns = np.array([list(names).index(name) for name in LOGIT_FEATURES])
        design = cls._design(X, columns)
        keep = ~np.isnan(design).any(axis=1)
        design, labels = design[keep], np.asarray(y)[keep]
        mean = design.mean(axis=0)
        scale = np.where(design.std(axis=0) > 0, design.std(axis=0), 1.0)
        z = np.column_stack([np.ones(len(design)), (design - mean) / scale])
        onehot = np.zeros((len(labels), n_classes))
        onehot[np.arange(len(labels)), labels] = 1.0
        d = z.shape[1]

        def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
            w = flat.reshape(d, n_classes)
            logits = z @ w
            logits -= logits.max(axis=1, keepdims=True)
            log_norm = np.log(np.exp(logits).sum(axis=1, keepdims=True))
            log_p = logits - log_norm
            penalty = 0.5 * l2 * float((w[1:] ** 2).sum())
            loss = -float((onehot * log_p).sum()) / len(z) + penalty
            grad = z.T @ (np.exp(log_p) - onehot) / len(z)
            grad[1:] += l2 * w[1:]
            return loss, grad.ravel()

        fitted = minimize(objective, np.zeros(d * n_classes), jac=True, method="L-BFGS-B")
        return cls(names, columns, mean, scale, fitted.x.reshape(d, n_classes))

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        design = self._design(X, self.columns)
        missing = np.isnan(design).any(axis=1)
        z = (np.nan_to_num(design) - self.mean) / self.scale
        logits = z @ self.weights[1:] + self.weights[0]
        logits -= logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(axis=1, keepdims=True)
        p[missing] = np.nan
        return p


class LogitBank(GbmBank):
    """Same bucket logic as :class:`GbmBank` but a small pooled logit that is refitted in memory (no disk cache)."""

    def model(self, market: Market, params: Mapping[str, Any], cut_ns: int) -> Any | None:
        key = (market.name, params_key(params), int(cut_ns))
        if key in self._models:
            return self._models[key]
        if not self.usable(cut_ns):
            self._models[key] = None
            return None
        rows = self.n_rows_before(cut_ns)
        fitted = PooledLogit.fit(self.matrix.X[:rows], self.labels(market)[:rows], len(market.classes), self.matrix.names)
        self._models[key] = fitted
        return fitted


def _kickoff_ns(frame: pd.DataFrame) -> np.ndarray:
    return frame["kickoff"].dt.as_unit("ns").dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64")


class PiGbmEngine:
    """Shared state of the pi-ratings + boosting model: as-of store, training matrix, boosters, feature cache."""

    def __init__(
        self,
        matches: pd.DataFrame,
        team_stats: pd.DataFrame | None = None,
        *,
        latency_hours: float = 3.0,
        settings: GbmSettings | None = None,
        pi_config: PiRatingConfig | None = None,
        cache_dir: str | Path | None = None,
        kind: str = "gbm",
    ) -> None:
        if kind not in ("gbm", "logit"):
            raise ValueError("kind must be 'gbm' or 'logit'")
        self.latency_ns = int(latency_hours * _NS_PER_HOUR)
        self.settings = settings or GbmSettings()
        self.store = AsOfStore(matches, team_stats, pi_config=pi_config)
        self.matrix = self.store.training_matrix(self.latency_ns)
        bank_class = GbmBank if kind == "gbm" else LogitBank
        self.bank = bank_class(self.matrix, self.settings, latency_ns=self.latency_ns, cache_dir=cache_dir)
        self._features: dict[tuple[int, int, int, int], tuple[np.ndarray, np.ndarray]] = {}

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.store.names

    def matchday_cutoff(self, frame: pd.DataFrame) -> int:
        """Training cut-off of the matchday: its first kickoff minus the result latency (the framework's own)."""
        return int(_kickoff_ns(frame).min()) - self.latency_ns

    def frame_features(self, frame: pd.DataFrame, cutoff_ns: int) -> np.ndarray:
        """Pre-kickoff features of the rows of ``frame`` as of ``cutoff_ns`` (cached per matchday)."""
        ids = frame["fixture_id"].to_numpy(dtype=np.int64)
        key = (int(cutoff_ns), len(ids), int(ids.sum()), hash(ids.tobytes()))
        hit = self._features.get(key)
        if hit is not None:
            return hit[0]
        kickoff = _kickoff_ns(frame)
        x = self.store.features(
            league_code=frame["league_code"].to_numpy(dtype=object),
            season=frame["season"].to_numpy(dtype=np.int64),
            home_team_id=frame["home_team_id"].to_numpy(dtype=np.int64),
            away_team_id=frame["away_team_id"].to_numpy(dtype=np.int64),
            kickoff_ns=kickoff,
            cutoff_ns=int(cutoff_ns),
        ).astype(np.float32)
        self._features[key] = (x, ids)
        return x

    def forecast_rows(self, cut_ns: int) -> tuple[np.ndarray, np.ndarray]:
        """Feature rows and fixture ids of every matchday forecast so far that used the booster of ``cut_ns``."""
        parts = [(x, ids) for (cutoff, *_), (x, ids) in self._features.items() if self.bank.cut_for(cutoff) == cut_ns]
        if not parts:
            return np.empty((0, len(self.feature_names)), dtype=np.float32), np.empty(0, dtype=np.int64)
        return np.vstack([x for x, _ in parts]), np.concatenate([ids for _, ids in parts])

    def outcomes(self, market: Market, fixture_ids: np.ndarray) -> np.ndarray:
        """Outcome class of finished matches by fixture id (NaN for an unknown fixture)."""
        goals = pd.DataFrame({"home_goals": self.store.home_goals, "away_goals": self.store.away_goals})
        outcome = pd.Series(market.outcome(goals), index=self.store.fixture_id)
        return outcome.reindex(np.asarray(fixture_ids, dtype=np.int64)).to_numpy(dtype=float)

    def required_cuts(self, group_start_ns: Sequence[int]) -> list[int]:
        """Booster buckets needed to forecast matchdays that start at the given instants."""
        cuts = {self.bank.cut_for(int(start) - self.latency_ns) for start in group_start_ns}
        return sorted(c for c in cuts if self.bank.usable(c))

    def predict(self, market: Market, params: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
        k = len(market.classes)
        if len(frame) == 0:
            return np.empty((0, k))
        cutoff = self.matchday_cutoff(frame)
        booster = self.bank.model(market, params, self.bank.cut_for(cutoff))
        if booster is None:
            return np.full((len(frame), k), np.nan)
        x = self.frame_features(frame, cutoff)
        proba = np.asarray(booster.predict_proba(x), dtype=float)
        proba = np.clip(proba, _PROBABILITY_FLOOR, None)
        return proba / proba.sum(axis=1, keepdims=True)


class PiGbmModel:
    """Protocol model: fit(train) / predict(frame) on top of a shared :class:`PiGbmEngine`.

    ``fit`` only records how far the training frame reaches; ``predict`` checks
    that this is before the matchday's cut-off and then forecasts from the
    store's features and the pooled booster of the matching 28-day bucket.
    """

    def __init__(self, engine: PiGbmEngine, market: Market, params: Mapping[str, Any]) -> None:
        self.engine = engine
        self.market = market
        self.classes = market.classes
        self.params = dict(params)
        self._fitted = False
        self._train_reaches_ns: int | None = None

    def fit(self, train: pd.DataFrame) -> "PiGbmModel":
        self._train_reaches_ns = int(_kickoff_ns(train).max()) if len(train) else None
        self._fitted = True
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("fit must be called before predict")
        if len(frame) and self._train_reaches_ns is not None:
            cutoff = self.engine.matchday_cutoff(frame)
            if self._train_reaches_ns >= cutoff:
                raise ValueError(
                    "the training frame contains matches at or after the matchday cut-off "
                    "(result latency mismatch between the framework and the model)"
                )
        return self.engine.predict(self.market, self.params, frame)


def pi_gbm_spec(
    engine: PiGbmEngine,
    market: Market,
    grid: Mapping[str, Sequence[Any]] | None = None,
    *,
    name: str = "pi_gbm",
) -> ModelSpec:
    """Protocol spec: the pooled booster with a small learning rate x depth grid chosen by the framework."""
    grid = dict(grid or DEFAULT_GRID)

    def factory(**params: Any) -> PiGbmModel:
        return PiGbmModel(engine, market, params)

    return ModelSpec(name, factory, param_grid=grid)


def pi_logit_spec(engine: PiGbmEngine, market: Market, *, name: str = "pi_ratings") -> ModelSpec:
    """Protocol spec of the ratings-only logit (``engine`` must be built with ``kind="logit"``); no grid."""
    if not isinstance(engine.bank, LogitBank):
        raise ValueError("pi_logit_spec needs an engine built with kind='logit'")
    return ModelSpec(name, lambda: PiGbmModel(engine, market, {}))


def grid_combinations(grid: Mapping[str, Sequence[Any]] | None = None) -> list[dict[str, Any]]:
    """The hyper-parameter combinations of a grid, in the framework's order."""
    return ModelSpec("grid", lambda **p: None, param_grid=dict(grid or DEFAULT_GRID)).combinations()  # type: ignore[arg-type]


def gain_importance(booster: Any, names: Sequence[str]) -> pd.Series:
    """CatBoost prediction-values-change importance (share of the total, sums to 100)."""
    values = np.asarray(booster.get_feature_importance(), dtype=float)
    return pd.Series(values, index=list(names)).sort_values(ascending=False)


def permutation_importance(
    predict_loss,
    X: np.ndarray,
    names: Sequence[str],
    *,
    n_repeats: int = 3,
    seed: int = 0,
) -> pd.Series:
    """Increase of ``predict_loss(X)`` (mean log-loss) when one feature column is shuffled."""
    rng = np.random.default_rng(seed)
    base = float(predict_loss(X))
    out = {}
    for j, name in enumerate(names):
        deltas = []
        for _ in range(n_repeats):
            shuffled = X.copy()
            shuffled[:, j] = shuffled[rng.permutation(len(X)), j]
            deltas.append(float(predict_loss(shuffled)) - base)
        out[name] = float(np.mean(deltas))
    return pd.Series(out).sort_values(ascending=False)


__all__ = [
    "DEFAULT_GRID",
    "GbmBank",
    "GbmSettings",
    "LogitBank",
    "PiGbmEngine",
    "PiGbmModel",
    "fit_booster",
    "gain_importance",
    "grid_combinations",
    "params_key",
    "permutation_importance",
    "pi_gbm_spec",
    "pi_logit_spec",
    "PooledLogit",
]
