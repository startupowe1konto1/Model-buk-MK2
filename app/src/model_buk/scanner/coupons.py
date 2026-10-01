"""Coupons of two or three legs built from the value list.

A coupon combines value legs from *different* fixtures at the *same* bookmaker (at most one leg per fixture).  The
stake tax is charged once on the whole coupon while the per-leg edges compound, so for legs from different
matches (assumed independent)::

    hit probability   = prod(p_fair_i)
    EV_net (tau)      = tau * prod(p_fair_i * odd_i) - 1
    EV_net lower (tau) = tau * prod(p_lower_i * odd_i) - 1        with p_lower_i = p_fair_i - z * p_se_i

The product of the legs' lower bounds is a lower bound of the coupon's win probability times its odds, exactly as
in :func:`model_buk.markets.pricing.accumulator_ev_net`.  Three legs with a 5% edge each give
``1.05 ** 3 * 0.88 - 1 = +1.87%`` although a single 5% bet loses after the 12% stake tax.

Refunds.  On integer and quarter lines a stake can come back (push) or half come back, and ``p_fair`` is then the
break-even probability (see :mod:`model_buk.scanner.engine`).  With ``refund_share`` ``b`` of a leg the mean
return factor of the leg is ``1 + (1 - b) * (p * odd - 1)`` (a pushed leg drops out of the coupon with factor one)
and its win share is ``(1 - b) * p``, so the two products above use those instead of ``p * odd`` and ``p``; for
legs without refunds (``b = 0``) nothing changes.  A leg whose refund share is unknown (NaN) is not used.

A coupon is kept when its lower-bound EV at ``coupon_keep_tau`` is strictly above ``min_coupon_ev`` and its hit
probability reaches ``min_hit``.  Coupons are ranked by the lower-bound EV (value first) and then by the hit
probability; the best ``top_n_per_bookmaker`` per bookmaker are proposed (``is_top``, no leg appears in more than
``top_max_leg_reuse`` of them, so the proposals are not one bet in several disguises).  ``n_passing`` counts *every*
coupon that passes; the frame holds all of them unless ``log_cap_per_bookmaker`` limits it to the best few hundred
per bookmaker (the default keeps an hourly register small).  To avoid a combinatorial explosion the legs of a bookmaker are pruned to the best
``top_k_legs`` by lower-bound EV plus the best ``top_k_hit_legs`` by win probability (one candidate of each kind
per fixture) before they are combined.

Tiers and tax regimes (:func:`build_coupons_by_tier`, what a scan uses)
-----------------------------------------------------------------------
Coupons never mix market tiers (:mod:`model_buk.scanner.tiers`): the legs of ``liquid`` fixtures and those of
``thin`` fixtures are combined separately, and every coupon carries its ``tier``.  Per tier there are two groups:
the coupons that pass at ``coupon_keep_tau`` (the full 12% stake tax; the proposals) and, shown apart, those that
pass only under a lower tax (at the loosest regime of ``taus``, 1.00, but not at ``coupon_keep_tau``).  Every
coupon carries ``regime``, the label of the strictest tax regime at which its lower-bound EV is positive
(``t088``, ``t094`` or ``t100``); each group has its own diversified top list per bookmaker.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from model_buk.markets.pricing import normalise_bookmaker_name
from model_buk.scanner.engine import ScannerConfig, effective_tau
from model_buk.scanner.settlement import tau_label

COUPON_LEG_COLUMNS: tuple[str, ...] = (
    "coupon_id",
    "leg_no",
    "leg_key",
    "bookmaker",
    "fixture_id",
    "kickoff_utc",
    "market",
    "period",
    "line",
    "selection",
    "odd",
    "p_fair",
    "p_lower",
    "refund_share",
    "fair_source",
    "tier",
)

_REQUIRED = ("fixture_id", "bookmaker", "market", "period", "line", "selection", "odd", "p_fair", "p_lower", "leg_key")


@dataclass
class CouponSet:
    """Result of :func:`build_coupons`.

    ``coupons`` holds the passing coupons (one row each, with ``rank`` per bookmaker, ``is_top`` and
    ``is_logged``), all of them when no log cap applies and otherwise the best ``log_cap_per_bookmaker`` per
    bookmaker; ``legs`` holds the legs of those coupons; ``n_evaluated`` counts the combinations from distinct
    fixtures that were priced and ``n_passing`` all that passed (also those not materialised).
    """

    coupons: pd.DataFrame
    legs: pd.DataFrame
    n_evaluated: int = 0
    n_passing: int = 0
    n_passing_lower: int = 0
    by_tier: dict = field(default_factory=dict)
    keep_label: str | None = None

    @property
    def top(self) -> pd.DataFrame:
        """The listed coupons: the best ``top_n_per_bookmaker`` of every bookmaker (of every group and tier)."""
        if self.coupons.empty:
            return self.coupons
        return self.coupons[self.coupons["is_top"].astype(bool)]

    def proposed(self, tier: str | None = None, *, lower_tax: bool = False) -> pd.DataFrame:
        """Top coupons that pass at the full tax (``lower_tax=False``) or only at a lower tax, optionally of one tier.

        Without a ``regime`` column (a set from :func:`build_coupons`) every top coupon passes at the full tax.
        """
        top = self.top
        if top.empty:
            return top
        if "regime" in top.columns and self.keep_label is not None:
            full = top["regime"] == self.keep_label
            top = top[~full] if lower_tax else top[full]
        elif lower_tax:
            return top.iloc[0:0]
        if tier is not None and "tier" in top.columns:
            top = top[top["tier"] == tier]
        return top

    def legs_of(self, coupon_id: str) -> pd.DataFrame:
        if self.legs.empty:
            return self.legs
        return self.legs[self.legs["coupon_id"] == coupon_id].sort_values("leg_no")


def coupon_id(bookmaker: str, leg_keys: list[str]) -> str:
    """Stable id of a coupon: bookmaker plus the sorted leg keys (order of the legs does not matter)."""
    text = normalise_bookmaker_name(bookmaker) + "||" + "||".join(sorted(leg_keys))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _empty_set(config: ScannerConfig) -> CouponSet:
    columns = ["coupon_id", "bookmaker", "n_legs", "odds_total", "p_hit", "p_hit_lower"]
    for tau in config.taus:
        label = tau_label(tau)
        columns += [f"ev_net_{label}", f"ev_net_lower_{label}", f"pass_{label}"]
    columns += ["kickoff_first", "kickoff_last", "leg_keys", "rank", "is_top", "is_logged"]
    return CouponSet(pd.DataFrame(columns=columns), pd.DataFrame(columns=list(COUPON_LEG_COLUMNS)), 0, 0)


def _candidates(group: pd.DataFrame, config: ScannerConfig) -> pd.DataFrame:
    """Prune the legs of one bookmaker before combining (see the module docstring)."""
    group = group.copy()
    group["_ev_lower"] = group["_f_low"] - 1.0
    by_ev = group.sort_values(["_ev_lower", "_p_win", "leg_key"], ascending=[False, False, True], kind="mergesort")
    by_hit = group.sort_values(["_p_win", "_ev_lower", "leg_key"], ascending=[False, False, True], kind="mergesort")
    best_ev = by_ev.drop_duplicates("fixture_id", keep="first").head(config.top_k_legs)
    best_hit = by_hit.drop_duplicates("fixture_id", keep="first").head(config.top_k_hit_legs)
    merged = pd.concat([best_ev, best_hit]).drop_duplicates("leg_key", keep="first")
    return merged.sort_values("leg_key", kind="mergesort").reset_index(drop=True)


def build_coupons(
    value_legs: pd.DataFrame,
    config: ScannerConfig | None = None,
    *,
    keep_tau: float | None = None,
    fail_tau: float | None = None,
) -> CouponSet:
    """Build the passing 2- and 3-leg coupons from a value list (see the module docstring).

    ``value_legs`` needs ``fixture_id, bookmaker, market, period, line, selection, odd, p_fair, p_lower,
    leg_key`` (the output of :func:`model_buk.scanner.engine.select_value_picks` has them) and may have
    ``refund_share`` (missing column: no refunds) and ``tier``.  Rows that are model only, at a reference
    bookmaker, that lack a probability or a price, or whose refund share is unknown (NaN) are ignored.
    ``keep_tau`` (default ``config.coupon_keep_tau``) is the regime a coupon must pass at; with ``fail_tau`` a
    coupon that also passes at that (stricter) regime is left out (the "only at a lower tax" group).
    """
    config = config or ScannerConfig()
    keep_tau = config.coupon_keep_tau if keep_tau is None else float(keep_tau)
    if value_legs is None or len(value_legs) == 0:
        return _empty_set(config)
    missing = [c for c in _REQUIRED if c not in value_legs.columns]
    if missing:
        raise ValueError(f"value legs are missing columns: {', '.join(missing)}")
    legs = value_legs.copy()
    if "is_model_only" in legs.columns:
        legs = legs[~legs["is_model_only"].astype(bool)]
    reference = {normalise_bookmaker_name(b) for b in config.reference_bookmakers}
    legs = legs[~legs["bookmaker"].map(lambda b: normalise_bookmaker_name(b) in reference).astype(bool)]
    for column in ("p_fair", "p_lower", "odd"):
        legs[column] = pd.to_numeric(legs[column], errors="coerce")
    legs = legs[legs[["p_fair", "p_lower", "odd"]].notna().all(axis=1) & (legs["odd"] > 1.0)]
    if "refund_share" not in legs.columns:
        legs["refund_share"] = 0.0
    legs["refund_share"] = pd.to_numeric(legs["refund_share"], errors="coerce")
    legs = legs[legs["refund_share"].between(0.0, 1.0)]  # unknown (NaN) refund share: the leg's return is not known
    keep = 1.0 - legs["refund_share"]
    legs["_p_win"] = keep * legs["p_fair"]
    legs["_p_win_low"] = keep * legs["p_lower"]
    legs["_f_point"] = 1.0 + keep * (legs["p_fair"] * legs["odd"] - 1.0)
    legs["_f_low"] = 1.0 + keep * (legs["p_lower"] * legs["odd"] - 1.0)
    if "fair_source" not in legs.columns:
        legs["fair_source"] = np.nan
    if "kickoff_utc" not in legs.columns:
        legs["kickoff_utc"] = pd.NaT
    if "tier" not in legs.columns:
        legs["tier"] = np.nan
    if legs.empty:
        return _empty_set(config)

    keep_label = tau_label(keep_tau)
    fail_label = None if fail_tau is None else tau_label(fail_tau)
    labels = {tau_label(t) for t in config.taus}
    if keep_label not in labels or (fail_label is not None and fail_label not in labels):
        raise ValueError("keep_tau and fail_tau must be among config.taus")
    materialize = max(config.top_n_per_bookmaker, config.log_cap_per_bookmaker) if config.log_cap_per_bookmaker is not None else None
    max_size = max(config.coupon_sizes)
    frames: list[pd.DataFrame] = []
    tables: dict[str, pd.DataFrame] = {}
    n_evaluated = 0
    n_passing = 0

    for bookmaker, group in legs.groupby("bookmaker", sort=True):
        candidates = _candidates(group, config)
        tables[bookmaker] = candidates
        m = len(candidates)
        fixture = pd.factorize(candidates["fixture_id"])[0]
        p_win = candidates["_p_win"].to_numpy(dtype=float)
        p_win_low = candidates["_p_win_low"].to_numpy(dtype=float)
        f_point = candidates["_f_point"].to_numpy(dtype=float)
        f_low = candidates["_f_low"].to_numpy(dtype=float)
        odd = candidates["odd"].to_numpy(dtype=float)
        parts: list[dict[str, np.ndarray]] = []
        for size in config.coupon_sizes:
            if m < size:
                continue
            combos = np.array(list(itertools.combinations(range(m), size)), dtype=np.int32)
            distinct = np.ones(len(combos), dtype=bool)
            for a, b in itertools.combinations(range(size), 2):
                distinct &= fixture[combos[:, a]] != fixture[combos[:, b]]
            combos = combos[distinct]
            n_evaluated += len(combos)
            if not len(combos):
                continue
            hit = p_win[combos].prod(axis=1)
            product_point = f_point[combos].prod(axis=1)
            product_lower = f_low[combos].prod(axis=1)
            odds_total = odd[combos].prod(axis=1)
            evs: dict[str, np.ndarray] = {}
            for tau in config.taus:
                label = tau_label(tau)
                tau_row = effective_tau(tau, odds_total, config.stake_pln)
                evs[f"ev_net_{label}"] = tau_row * product_point - 1.0
                evs[f"ev_net_lower_{label}"] = tau_row * product_lower - 1.0
            passing = (evs[f"ev_net_lower_{keep_label}"] > config.min_coupon_ev) & (hit >= config.min_hit)
            if config.max_coupon_odds is not None:
                passing &= odds_total <= config.max_coupon_odds
            if fail_label is not None:
                passing &= ~(evs[f"ev_net_lower_{fail_label}"] > config.min_coupon_ev)
            if not passing.any():
                continue
            padded = np.full((int(passing.sum()), max_size), -1, dtype=np.int32)
            padded[:, :size] = combos[passing]
            part = {
                "rows": padded,
                "n_legs": np.full(len(padded), size),
                "odds_total": odds_total[passing],
                "p_hit": hit[passing],
                "p_hit_lower": p_win_low[combos[passing]].prod(axis=1),
            }
            for key, values in evs.items():
                part[key] = values[passing]
            parts.append(part)
        if not parts:
            continue
        merged = {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}
        n_passing += len(merged["p_hit"])
        # Rank: lower-bound EV first (value comes first), then hit probability, then the leg positions (deterministic).
        keys = [merged["rows"][:, i] for i in reversed(range(max_size))]
        order = np.lexsort((*keys, -merged["p_hit"], -merged[f"ev_net_lower_{keep_label}"]))
        if materialize is not None:
            order = order[:materialize]
        rows = merged["rows"][order]
        frame = pd.DataFrame({key: values[order] for key, values in merged.items() if key != "rows"})
        frame.insert(0, "bookmaker", bookmaker)
        for tau in config.taus:
            label = tau_label(tau)
            frame[f"pass_{label}"] = frame[f"ev_net_lower_{label}"] > config.min_coupon_ev
        frame["rank"] = np.arange(1, len(frame) + 1)
        frame["rows_idx"] = [tuple(int(i) for i in row if i >= 0) for row in rows]
        leg_keys = candidates["leg_key"].to_numpy()
        kickoffs = candidates["kickoff_utc"].to_numpy()
        frame["leg_keys"] = [" || ".join(sorted(leg_keys[list(row)])) for row in frame["rows_idx"]]
        frame["coupon_id"] = [coupon_id(bookmaker, list(leg_keys[list(row)])) for row in frame["rows_idx"]]
        frame["kickoff_first"] = [min(pd.Timestamp(kickoffs[i]) for i in row) for row in frame["rows_idx"]]
        frame["kickoff_last"] = [max(pd.Timestamp(kickoffs[i]) for i in row) for row in frame["rows_idx"]]
        frame["is_top"] = _diversified_top(frame["rows_idx"].tolist(), config.top_n_per_bookmaker, config.top_max_leg_reuse)
        cap = config.log_cap_per_bookmaker
        frame["is_logged"] = True if cap is None else frame["rank"] <= cap
        frames.append(frame)

    if not frames:
        result = _empty_set(config)
        result.n_evaluated = n_evaluated
        return result

    coupons = pd.concat(frames, ignore_index=True)
    wanted = coupons[coupons["is_top"] | coupons["is_logged"]]
    rows_out: list[dict] = []
    for record in wanted.itertuples(index=False):
        table = tables[record.bookmaker]
        order = sorted(record.rows_idx, key=lambda i: table.iloc[i]["leg_key"])
        for leg_no, position in enumerate(order, start=1):
            leg = table.iloc[position]
            rows_out.append(
                {
                    "coupon_id": record.coupon_id,
                    "leg_no": leg_no,
                    "leg_key": leg["leg_key"],
                    "bookmaker": record.bookmaker,
                    "fixture_id": int(leg["fixture_id"]),
                    "kickoff_utc": leg["kickoff_utc"],
                    "market": leg["market"],
                    "period": leg["period"],
                    "line": leg["line"],
                    "selection": leg["selection"],
                    "odd": float(leg["odd"]),
                    "p_fair": float(leg["p_fair"]),
                    "p_lower": float(leg["p_lower"]),
                    "refund_share": float(leg["refund_share"]),
                    "fair_source": leg["fair_source"],
                    "tier": leg["tier"],
                }
            )
    leg_frame = pd.DataFrame(rows_out, columns=list(COUPON_LEG_COLUMNS))
    coupons = coupons.drop(columns=["rows_idx"])
    return CouponSet(coupons, leg_frame, n_evaluated, n_passing, keep_label=tau_label(config.coupon_keep_tau))


def strictest_regime(frame: pd.DataFrame, taus: tuple[float, ...], min_ev: float) -> list[str | None]:
    """Label of the strictest (lowest) tax regime at which each coupon's lower-bound EV is above ``min_ev``."""
    ordered = sorted(taus)
    out: list[str | None] = []
    for _, row in frame.iterrows():
        label = None
        for tau in ordered:
            value = row.get(f"ev_net_lower_{tau_label(tau)}")
            if value is not None and value == value and float(value) > min_ev:
                label = tau_label(tau)
                break
        out.append(label)
    return out


def build_coupons_by_tier(value_legs: pd.DataFrame, config: ScannerConfig | None = None) -> CouponSet:
    """Coupons of a scan: per market tier, the full-tax group and the lower-tax-only group (module docstring).

    The result's ``coupons`` carry ``tier`` and ``regime``; ``n_passing`` counts the coupons that pass at the full
    tax (``coupon_keep_tau``), ``n_passing_lower`` those that pass only at a lower one, ``by_tier`` both per tier
    and ``n_evaluated`` the combinations priced (once per tier).
    """
    config = config or ScannerConfig()
    keep_label = tau_label(config.coupon_keep_tau)
    empty = _empty_set(config)
    empty.coupons["tier"] = pd.Series(dtype=object)
    empty.coupons["regime"] = pd.Series(dtype=object)
    empty.keep_label = keep_label
    if value_legs is None or len(value_legs) == 0:
        return empty
    legs = value_legs.copy()
    if "tier" not in legs.columns:
        legs["tier"] = "liquid"
    legs["tier"] = legs["tier"].fillna("thin")
    loosest = max(config.taus)
    coupon_frames: list[pd.DataFrame] = []
    leg_frames: list[pd.DataFrame] = []
    n_evaluated = n_passing = n_lower = 0
    by_tier: dict[str, dict[str, int]] = {}
    for tier, group in legs.groupby("tier", sort=True):
        full = build_coupons(group, config)
        parts = [full]
        lower = None
        if loosest > config.coupon_keep_tau:
            lower = build_coupons(group, config, keep_tau=loosest, fail_tau=config.coupon_keep_tau)
            parts.append(lower)
        n_evaluated += int(full.n_evaluated)
        n_passing += int(full.n_passing)
        n_lower += int(lower.n_passing) if lower is not None else 0
        by_tier[str(tier)] = {
            "passing": int(full.n_passing),
            "passing_lower": int(lower.n_passing) if lower is not None else 0,
            "top": int(full.coupons["is_top"].sum()) if len(full.coupons) else 0,
            "top_lower": int(lower.coupons["is_top"].sum()) if lower is not None and len(lower.coupons) else 0,
        }
        for part in parts:
            if len(part.coupons) == 0:
                continue
            frame = part.coupons.copy()
            frame["tier"] = str(tier)
            frame["regime"] = strictest_regime(frame, config.taus, config.min_coupon_ev)
            coupon_frames.append(frame)
            leg_frames.append(part.legs)
    if not coupon_frames:
        empty.n_evaluated = n_evaluated
        empty.by_tier = by_tier
        return empty
    coupons = pd.concat(coupon_frames, ignore_index=True)
    leg_frame = pd.concat(leg_frames, ignore_index=True)
    return CouponSet(coupons, leg_frame, n_evaluated, n_passing, n_lower, by_tier, keep_label)


def _diversified_top(rows: list[tuple[int, ...]], top_n: int, max_reuse: int) -> np.ndarray:
    """Flags of the proposed coupons: the best ``top_n`` in rank order in which no leg appears more than ``max_reuse`` times.

    Coupons built from the same few legs are one bet in several disguises, so the proposals (and the paper results
    that are computed on them) are spread over different legs.  ``rows`` are in rank order.
    """
    flags = np.zeros(len(rows), dtype=bool)
    uses: dict[int, int] = {}
    taken = 0
    for index, row in enumerate(rows):
        if taken >= top_n:
            break
        if any(uses.get(leg, 0) >= max_reuse for leg in row):
            continue
        for leg in row:
            uses[leg] = uses.get(leg, 0) + 1
        flags[index] = True
        taken += 1
    return flags
