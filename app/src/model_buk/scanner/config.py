"""Configuration of a scanner run: the value definition (hashed) and the operational settings (not hashed).

Two objects, on purpose:

* :class:`ScanConfig` holds **every threshold that decides what a value leg or a coupon is**: the tax regimes,
  the confidence margin, the plausibility guards, the coupon rules, the staleness limits and the settings of the
  fair price and of the models.  It extends :class:`model_buk.scanner.engine.ScannerConfig` (the engine and the
  coupon builder read their fields from it unchanged) with the fair-price and model settings, so
  :attr:`ScanConfig.config_id` -- a hash of *every* field -- changes whenever anything that can change a decision
  changes.  The paper register keeps its results per ``config_id``: a changed definition of value starts a new
  paper experiment and is never mixed with the old one.
* :class:`RunSettings` holds what only changes *how* a run is carried out: file locations, the API budget, the
  calendar window of the fixture directory, which outputs are written.  None of it can change a decision, so it
  is not part of the hash (raising the API budget must not start a new experiment).

Defaults of :class:`ScanConfig` (inherited ones are documented in full on ``ScannerConfig``)
-------------------------------------------------------------------------------------------
Value and tax
    ``taus = (0.88, 0.94, 1.00)`` stake-tax multipliers evaluated for every leg (Polish 12% tax, a degraded 6%
    regime, tax free); ``z = 1.645`` standard errors subtracted from the fair probability (one-sided 95%);
    ``min_ev = 0`` the lower-bound EV must be strictly above it; ``leg_tau = 1.00`` the regime that decides
    membership of the value list (the price beats the fair price with confidence; the tax is charged once per
    coupon); ``stake_pln = None`` no paper stake, so the winnings-PIT rule is off.
Model (secondary signal)
    ``model_delta = 0.02`` tolerance of ``model_agrees``; ``model_veto = False`` (our models are about 0.013
    log-loss worse than Pinnacle, so they never override a market price); ``model_only_se = 0.03`` standard error
    assumed for a leg only the model prices; ``include_model_only = True`` such legs form a separate, flagged
    research class that never enters the value list or a coupon.  ``use_models = True`` forecast every modelled
    fixture from the cached nightly fit; ``model_max_fit_age_hours = 24`` a fit older than one day is not used
    (the scan runs without models and says so); ``model_include_counts = True`` also corners and cards.
Plausibility guards
    ``max_edge = 0.20`` raw edge above which a leg is ``suspect``; ``max_p_se = 0.05`` fair-probability standard
    error above which it is ``suspect``; ``max_book_shift = 0.06`` largest allowed distance between the
    bookmaker's own de-vigged probability and the fair one.  Suspect legs are logged, never proposed.
Inputs and staleness
    ``reference_bookmakers = ("Pinnacle", "Betfair")`` price sources only, never proposed;
    ``max_quote_age_hours = 3`` a bookmaker board not seen for longer is skipped; ``min_minutes_to_kickoff = 10``;
    ``max_days_ahead = None`` no horizon: every upcoming match with odds is scanned.  The odds collector looks at
    today and the next two days every hour but at the days beyond only every six hours, so a match more than
    ``far_lead_hours = 48`` away is allowed a board ``max_quote_age_far_hours = 8`` old (otherwise more than half
    of all upcoming matches would be out of the pool half of the time).  ``fair_max_age_minutes = 180`` and
    ``fair_max_age_far_minutes = 480`` are the same two staleness bounds for the boards the fair price is taken
    from.
Fair price (:func:`model_buk.scanner.fair.fair_prices`)
    ``fair_method = "power"`` de-vig method; ``fair_betfair_commission = 0`` (Betfair in this feed is the
    sportsbook, not the exchange); ``fair_min_consensus_books = 3`` books needed for a consensus price;
    ``fair_derive = True`` derive legs Pinnacle does not quote from the score matrix fitted to Pinnacle;
    ``fair_score_model = "dc"`` Dixon-Coles score matrix; ``fair_se_model`` the standard-error model;
    ``include_suspect_ladders = False`` drop bookmaker ladders that are flat against Pinnacle.
Coupons
    ``coupon_sizes = (2, 3)`` legs; ``min_hit = 0.30`` minimum win probability; ``min_coupon_ev = 0`` threshold of
    the lower-bound EV at ``coupon_keep_tau = 0.88`` (a proposed coupon stays profitable under the full stake
    tax); ``top_k_legs = 60`` (K) and ``top_k_hit_legs = 30`` legs per bookmaker kept before combining;
    ``top_n_per_bookmaker = 20`` (N) proposed coupons per bookmaker, ``top_max_leg_reuse = 3``;
    ``log_cap_per_bookmaker = 500`` coupons written to the register per bookmaker and scan.
Page and log
    ``top_value_rows = 200`` value legs on the page; ``log_change_tolerance = 0.005`` and
    ``log_heartbeat_hours = 6`` for the change-only leg log of the register.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from model_buk.markets.pricing import DEVIG_METHODS
from model_buk.scanner.engine import DATA_ROOT, ScannerConfig
from model_buk.scanner.fair import (
    DEFAULT_BETFAIR_COMMISSION,
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_METHOD,
    DEFAULT_MIN_CONSENSUS_BOOKS,
    SeModel,
)

DEFAULT_MAX_AGE_FAR_MINUTES: float = 480.0
"""Age bound of the boards the fair price uses for matches beyond ``far_lead_hours`` (8 hours, like the quotes)."""

SCORE_MODELS: tuple[str, ...] = ("dc", "poisson", "bivariate")
"""Score-matrix families :func:`model_buk.scanner.fair.fit_score_matrix` accepts."""


@dataclass(frozen=True)
class ScanConfig(ScannerConfig):
    """Every threshold of the scanner (see the module docstring for the defaults and their meaning).

    A :class:`~model_buk.scanner.engine.ScannerConfig` plus the settings of the fair price and of the models;
    :attr:`config_id` hashes all fields.  Pass it wherever a ``ScannerConfig`` is expected.
    """

    fair_method: str = DEFAULT_METHOD
    fair_betfair_commission: float = DEFAULT_BETFAIR_COMMISSION
    fair_min_consensus_books: int = DEFAULT_MIN_CONSENSUS_BOOKS
    fair_max_age_minutes: float | None = DEFAULT_MAX_AGE_MINUTES
    fair_max_age_far_minutes: float | None = DEFAULT_MAX_AGE_FAR_MINUTES
    fair_derive: bool = True
    fair_score_model: str = "dc"
    fair_se_model: SeModel = field(default_factory=SeModel)
    include_suspect_ladders: bool = False
    use_models: bool = True
    model_max_fit_age_hours: float = 24.0
    model_include_counts: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.fair_method not in DEVIG_METHODS:
            raise ValueError(f"fair_method must be one of {sorted(DEVIG_METHODS)}")
        if not (math.isfinite(self.fair_betfair_commission) and 0.0 <= self.fair_betfair_commission < 1.0):
            raise ValueError("fair_betfair_commission must be in [0, 1)")
        if int(self.fair_min_consensus_books) < 1:
            raise ValueError("fair_min_consensus_books must be at least 1")
        object.__setattr__(self, "fair_min_consensus_books", int(self.fair_min_consensus_books))
        if self.fair_max_age_minutes is not None and not self.fair_max_age_minutes > 0:
            raise ValueError("fair_max_age_minutes must be positive or None")
        if self.fair_max_age_far_minutes is not None and not self.fair_max_age_far_minutes > 0:
            raise ValueError("fair_max_age_far_minutes must be positive or None")
        if (
            self.fair_max_age_far_minutes is not None
            and self.fair_max_age_minutes is not None
            and self.fair_max_age_far_minutes < self.fair_max_age_minutes
        ):
            raise ValueError("fair_max_age_far_minutes must not be below fair_max_age_minutes")
        if self.fair_score_model not in SCORE_MODELS:
            raise ValueError(f"fair_score_model must be one of {SCORE_MODELS}")
        if not isinstance(self.fair_se_model, SeModel):
            raise ValueError("fair_se_model must be a SeModel")
        if not (math.isfinite(self.model_max_fit_age_hours) and self.model_max_fit_age_hours > 0):
            raise ValueError("model_max_fit_age_hours must be a positive number of hours")

    def fair_kwargs(self) -> dict[str, Any]:
        """Keyword arguments of :func:`model_buk.scanner.fair.fair_prices` for a *current* board."""
        return {
            "method": self.fair_method,
            "betfair_commission": self.fair_betfair_commission,
            "min_consensus_books": self.fair_min_consensus_books,
            "max_age_minutes": self.fair_max_age_minutes,
            "max_age_far_minutes": self.fair_max_age_far_minutes,
            "far_lead_hours": self.far_lead_hours,
            "derive": self.fair_derive,
            "score_model": self.fair_score_model,
            "se_model": self.fair_se_model,
        }

    def closing_fair_kwargs(self) -> dict[str, Any]:
        """The same for a *closing* board: no age bound (the last board before kickoff is the close by definition)."""
        return {**self.fair_kwargs(), "max_age_minutes": None}


def value_fields() -> tuple[str, ...]:
    """Names of every field of :class:`ScanConfig` (all of them are part of the hash)."""
    return tuple(f.name for f in fields(ScanConfig))


@dataclass(frozen=True)
class RunSettings:
    """How a run is carried out: paths, API budget, calendar window and outputs (not part of the config hash).

    Paths default to the standard layout under ``data_root`` (``Model-Buk-Data``):

    ``api-football-odds/odds.sqlite3`` (odds archive, read only), ``fixture-directory/`` (calendar and match
    statistics; refreshed by the run), ``api-football/tables/history.sqlite`` (finished matches, read only),
    ``api-football-prematch/prematch.sqlite3`` (fallback for fixture names, read only),
    ``scanner/model_cache`` (the nightly model fit, read only), ``scanner/`` (register, page, CSV, run log) and
    ``logs/`` (the error log).

    API (API-Football, the shared daily quota): ``api_enabled`` switches every request off; ``api_reserve = 1500``
    no request is made once the daily remaining quota is at or below it; ``max_requests_directory = 15`` (calendar
    refresh: today every run, the other days of the window only when their last listing is stale; one request per
    day), ``max_requests_details = 20`` (final score and statistics of finished matches, 20 matches per request)
    and ``max_requests_halves = 20`` (first-half corners and cards, one request per match) cap each step of one
    run; ``api_sleep`` seconds between requests.  ``directory_days_back`` / ``directory_days_ahead`` is the
    calendar window.  ``stats_min_age_hours`` a match is asked for statistics only this long after kickoff.

    Outputs: ``settle`` settles earlier paper legs and records closing line value; ``write_page``, ``write_csv``,
    ``write_forecast_log`` and ``write_run_log`` switch the files of the run on or off (the register is always
    written).
    """

    data_root: Path = DATA_ROOT
    odds_db: Path | None = None
    directory_root: Path | None = None
    history_db: Path | None = None
    prematch_db: Path | None = None
    model_cache_dir: Path | None = None
    out_dir: Path | None = None
    register_path: Path | None = None
    log_dir: Path | None = None
    api_enabled: bool = True
    api_reserve: int = 1500
    api_sleep: float = 0.15
    max_requests_directory: int = 15
    max_requests_details: int = 20
    max_requests_halves: int = 20
    directory_days_back: int = 3
    directory_days_ahead: int = 7
    stats_min_age_hours: float = 2.0
    settle: bool = True
    write_page: bool = True
    write_csv: bool = True
    write_forecast_log: bool = True
    write_run_log: bool = True

    def __post_init__(self) -> None:
        root = Path(self.data_root)
        object.__setattr__(self, "data_root", root)
        defaults = {
            "odds_db": root / "api-football-odds" / "odds.sqlite3",
            "directory_root": root / "fixture-directory",
            "history_db": root / "api-football" / "tables" / "history.sqlite",
            "prematch_db": root / "api-football-prematch" / "prematch.sqlite3",
            "model_cache_dir": root / "scanner" / "model_cache",
            "out_dir": root / "scanner",
            "log_dir": root / "logs",
        }
        for name, default in defaults.items():
            value = getattr(self, name)
            object.__setattr__(self, name, Path(value) if value is not None else default)
        if self.register_path is None:
            object.__setattr__(self, "register_path", Path(self.out_dir) / "scanner.sqlite")
        else:
            object.__setattr__(self, "register_path", Path(self.register_path))
        for name in ("api_reserve", "max_requests_directory", "max_requests_details", "max_requests_halves",
                     "directory_days_back", "directory_days_ahead"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.api_sleep < 0 or self.stats_min_age_hours < 0:
            raise ValueError("api_sleep and stats_min_age_hours must be non-negative")

    @property
    def directory_db(self) -> Path:
        from model_buk.ingestion.fixture_directory import DIRECTORY_DB

        return Path(self.directory_root) / DIRECTORY_DB

    @property
    def forecast_log_path(self) -> Path:
        return Path(self.out_dir) / "model_forecasts.sqlite"

    @property
    def run_log_path(self) -> Path:
        return Path(self.out_dir) / "runs.jsonl"

    def to_dict(self) -> dict[str, Any]:
        return {f.name: (str(v) if isinstance(v, Path) else v) for f in fields(self) for v in [getattr(self, f.name)]}


__all__ = ["RunSettings", "SCORE_MODELS", "ScanConfig", "value_fields"]
