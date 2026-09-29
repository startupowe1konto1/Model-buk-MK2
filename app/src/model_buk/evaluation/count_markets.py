"""Count markets: corners, cards, shots and shots on target (plan, days 5-7).

The goal/1X2 protocol of :mod:`model_buk.evaluation.protocol` is reused unchanged
(same outer test seasons, same refit cadence, same inner selection); this module
only adds the *data* and the *markets* for match statistics that bookmakers offer
as over/under lines:

- :data:`FAMILIES` -- one entry per modelled statistic (corners, yellow cards, shots, shots
  on target) with the lines that are evaluated for the match total and for each team;
- :func:`load_count_matches` -- the API-Football ``matches`` table joined with the
  per-team statistics (``team_match_stats``) and the card events (``events``),
  one row per match with ``home_<stat>`` / ``away_<stat>`` columns;
- :func:`apply_coverage_rule` -- the inclusion rule: a league-season counts for a
  statistic only if at least 90% of its finished matches carry the statistic for
  both teams (the same 90% threshold that ``tables/quality_report.md`` uses for its
  "Gaps" list); everywhere else the statistic stays NULL (never zero);
- :func:`count_markets` -- :class:`~model_buk.evaluation.framework.Market` objects
  (classes ``under`` < ``over``) whose ``outcome_columns`` are *both* count columns
  of the family, so the framework hides them from ``predict``;
- :func:`count_config` -- :data:`~model_buk.evaluation.protocol.COMPARISON_CONFIG`
  with every count column hidden from ``predict`` (a corners forecast must not see
  the shots of the match it forecasts either).

Definitions
-----------
``corners`` = ``team_match_stats.corners``; ``shots`` = ``total_shots``;
``sot`` = ``shots_on_goal``.  ``yellow`` is ``yellow_cards`` from
``team_match_stats`` and, where that value is NULL, the number of ``Yellow Card``
events of the team when the fixture has an event feed.  The provider leaves the
field NULL for most teams without a yellow card (in the event feed such teams have
no card events), so dropping every NULL would drop exactly the low-card matches;
where the NULL is a genuine gap (for instance Austrian 2. Liga) the event feed
carries the cards.  That reading holds only where the feed records cards at all: in
league-seasons where fewer than ``MIN_CARD_FEED_SHARE`` of the fixtures with a feed
have any card event (see :func:`card_feed_reliability`; mostly 2015-2018, where a
feed without cards would read as "zero cards" for every match) the events are ignored
and ``yellow`` is the provider's value or missing.  A provider foul count of 0 for both
teams is a placeholder and is read as missing.  ``booking`` are booking points as
bookmakers count them: 10 per yellow card and 25 per red card, a second yellow that
leads to a red card counting once (10 + 25), taken from the event feed; it is loaded
for completeness but not modelled (see ``AUX_STATS``).  ``fouls``, ``xg`` and ``red`` are auxiliary
statistics used as pre-match *form* features only.

Only matches with a known value for both teams have a known outcome: a team line
also requires the other team's count, so that every market of one family is
evaluated on exactly the same matches (and one model fit can serve them all).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from model_buk.evaluation.framework import DEFAULT_HIDDEN_COLUMNS, Market, WalkForwardConfig
from model_buk.evaluation.protocol import COMPARISON_CONFIG, HISTORY_DB, load_history_matches

MIN_COVERAGE = 0.90
# A league-season's event feed records cards if this share of its fixtures with a feed has a card event.
MIN_CARD_FEED_SHARE = 0.90

Scope = Literal["total", "home", "away"]


@dataclass(frozen=True)
class CountFamily:
    """One statistic offered as over/under lines.

    ``aux`` names the auxiliary statistics whose form is used as extra features
    for this statistic (see ``AUX_STATS``).  ``total_lines`` and ``team_lines``
    are the half-integer lines that are evaluated.
    """

    name: str
    label_pl: str
    total_lines: tuple[float, ...]
    team_lines: tuple[float, ...]
    aux: tuple[str, ...] = ()

    @property
    def home(self) -> str:
        return f"home_{self.name}"

    @property
    def away(self) -> str:
        return f"away_{self.name}"

    @property
    def columns(self) -> tuple[str, str]:
        return (self.home, self.away)


FAMILIES: dict[str, CountFamily] = {
    "corners": CountFamily("corners", "rzuty rożne", (8.5, 9.5, 10.5, 11.5), (3.5, 4.5, 5.5), aux=("shots", "goals")),
    "yellow": CountFamily("yellow", "żółte kartki", (2.5, 3.5, 4.5, 5.5), (1.5, 2.5), aux=("fouls", "goals")),
    "shots": CountFamily("shots", "strzały", (23.5, 25.5, 27.5), (10.5, 12.5, 14.5), aux=("goals",)),
    "sot": CountFamily("sot", "strzały celne", (7.5, 8.5, 9.5), (3.5, 4.5), aux=("shots", "goals")),
}

# Statistics that are never a market themselves.  ``fouls``, ``xg`` and ``red`` feed the form
# features; ``booking`` (points) is loaded and hidden from ``predict`` but not modelled: the
# points are a lattice (10 per yellow card, 25 per red card) that count regressions do not fit.
# (``goals`` are the goals of the match, already in the protocol's match table.)
AUX_STATS: tuple[str, ...] = ("fouls", "xg", "red", "booking")

# Families evaluated by default.
DEFAULT_FAMILIES: tuple[str, ...] = tuple(FAMILIES)

ALL_STATS: tuple[str, ...] = tuple(FAMILIES) + AUX_STATS
STAT_COLUMNS: tuple[str, ...] = tuple(col for stat in ALL_STATS for col in (f"home_{stat}", f"away_{stat}"))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

_CARD_EVENTS_SQL = """
WITH per_player AS (
    SELECT fixture_id, team_id, COALESCE(player_id, -seq) AS player_key,
           SUM(detail = 'Yellow Card') AS n_yellow, SUM(detail = 'Red Card') AS n_red
    FROM events
    WHERE type = 'Card'
    GROUP BY fixture_id, team_id, COALESCE(player_id, -seq)
)
SELECT fixture_id, team_id,
       SUM(n_yellow) AS ev_yellow,
       SUM(n_red) AS ev_red,
       SUM(CASE WHEN n_yellow >= 2 AND n_red >= 1 THEN 1 ELSE 0 END) AS ev_second_yellow
FROM per_player
GROUP BY fixture_id, team_id
"""

_STATS_SQL = """
SELECT fixture_id, team_id, side, total_shots, shots_on_goal, corners, fouls, yellow_cards, expected_goals
FROM team_match_stats
"""


def _connect_ro(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def load_count_matches(
    db_path: str | Path = HISTORY_DB, *, min_card_feed_share: float = MIN_CARD_FEED_SHARE
) -> pd.DataFrame:
    """Match table plus ``home_<stat>`` / ``away_<stat>`` columns (read-only; NULL stays NaN).

    Every fixture of ``matches`` is kept (the framework drops unfinished ones);
    a statistic the provider did not supply is NaN.  See the module docstring for
    the definition of each statistic.
    """

    matches = load_history_matches(db_path)
    with closing(_connect_ro(db_path)) as connection:
        stats = pd.read_sql_query(_STATS_SQL, connection)
        cards = pd.read_sql_query(_CARD_EVENTS_SQL, connection)
        feeds = pd.read_sql_query("SELECT DISTINCT fixture_id FROM events", connection)
    has_feed = set(feeds["fixture_id"].tolist())
    frame = matches.copy()
    frame["_has_feed"] = frame["fixture_id"].isin(has_feed)
    # The event feed is trusted for cards only in league-seasons where it demonstrably records them.
    card_feed = card_feed_reliability(frame, set(cards["fixture_id"].tolist()), min_share=min_card_feed_share)
    frame["_card_feed"] = card_feed
    source = {
        "shots": "total_shots",
        "sot": "shots_on_goal",
        "corners": "corners",
        "fouls": "fouls",
        "xg": "expected_goals",
        "yellow_stat": "yellow_cards",
    }
    for side in ("home", "away"):
        part = stats[stats["side"] == side].drop_duplicates("fixture_id").set_index("fixture_id")
        for name, column in source.items():
            frame[f"{side}_{name}"] = frame["fixture_id"].map(part[column]).astype(float)
        team = frame[f"{side}_team_id"]
        keyed = cards.set_index(["fixture_id", "team_id"])
        index = pd.MultiIndex.from_arrays([frame["fixture_id"], team])
        for name, column in (("ev_yellow", "ev_yellow"), ("ev_red", "ev_red"), ("ev_second", "ev_second_yellow")):
            counts = keyed[column].reindex(index).to_numpy(dtype=float)
            # A fixture with a card-recording event feed and no card event of the team has zero cards of
            # the team; where the feed does not record cards at all the events say nothing (missing).
            trusted = frame["_card_feed"].to_numpy()
            counts = np.where(np.isnan(counts) & frame["_has_feed"].to_numpy() & trusted, 0.0, counts)
            counts = np.where(trusted, counts, np.nan)
            frame[f"{side}_{name}"] = counts
        frame[f"{side}_yellow"] = frame[f"{side}_yellow_stat"].fillna(frame[f"{side}_ev_yellow"])
        # Red cards and booking points come from the event feed only.
        frame[f"{side}_red"] = frame[f"{side}_ev_red"]
        frame[f"{side}_booking"] = (
            10.0 * (frame[f"{side}_ev_yellow"] - frame[f"{side}_ev_second"]) + 25.0 * frame[f"{side}_ev_red"]
        )
    # A provider foul count of 0 for both teams is a placeholder for "not recorded" (a real match has fouls).
    no_fouls = (frame["home_fouls"] == 0) & (frame["away_fouls"] == 0)
    frame.loc[no_fouls, ["home_fouls", "away_fouls"]] = np.nan
    drop = [c for c in frame.columns if c.endswith(("_yellow_stat", "_ev_yellow", "_ev_red", "_ev_second"))]
    frame = frame.drop(columns=[*drop, "_has_feed", "_card_feed"])
    return frame


def card_feed_reliability(
    matches: pd.DataFrame, card_fixtures: set, *, min_share: float = MIN_CARD_FEED_SHARE, finished_status: str = "FT"
) -> np.ndarray:
    """Per match: does the event feed of its league-season record cards (True) or not (False)?

    In some league-seasons (mostly 2015-2018, and a few later ones) the event feed
    exists but has no card events for most matches, while the provider's ``yellow_cards``
    is empty too.  Reading "feed without a card event" as "zero cards" would then turn
    missing data into zeros.  A league-season's feed counts as card-recording when at
    least ``min_share`` of its finished fixtures with an event feed contain at least one
    card event (real league-seasons sit at 93-99%; the broken ones between 0% and 90%).
    ``matches`` needs ``fixture_id``, ``league_code``, ``season``, ``status_short`` and
    ``_has_feed``; ``card_fixtures`` are the fixtures with any card event.
    """

    finished = (matches["status_short"] == finished_status).to_numpy()
    with_feed = matches["_has_feed"].to_numpy() & finished
    with_card = with_feed & matches["fixture_id"].isin(card_fixtures).to_numpy()
    keys = pd.MultiIndex.from_arrays([matches["league_code"], matches["season"]])
    n_feed = pd.Series(with_feed.astype(float), index=keys).groupby(level=[0, 1]).sum()
    n_card = pd.Series(with_card.astype(float), index=keys).groupby(level=[0, 1]).sum()
    share = (n_card / n_feed.where(n_feed > 0)).fillna(0.0)
    reliable = share >= min_share
    return reliable.reindex(keys).fillna(False).to_numpy(dtype=bool)


def coverage_table(
    matches: pd.DataFrame, families: Iterable[str] | None = None, *, finished_status: str = "FT"
) -> pd.DataFrame:
    """Share of finished matches per league-season with both teams' value present.

    Long table with one row per league, season and family: ``n_finished``,
    ``n_present`` and ``share``.  Computed the same way as the coverage columns of
    ``quality_report.md`` (finished matches; value present for both teams), but per
    season instead of per multi-season bucket.
    """

    names = tuple(families) if families is not None else tuple(FAMILIES)
    done = matches[matches["status_short"] == finished_status]
    rows = []
    grouped = done.groupby(["league_code", "season"], sort=True)
    sizes = grouped.size()
    for name in names:
        present = (done[f"home_{name}"].notna() & done[f"away_{name}"].notna()).groupby(
            [done["league_code"], done["season"]]
        ).sum()
        for (league, season), n_finished in sizes.items():
            n_present = int(present.loc[(league, season)])
            rows.append(
                {
                    "family": name,
                    "league_code": league,
                    "season": int(season),
                    "n_finished": int(n_finished),
                    "n_present": n_present,
                    "share": n_present / n_finished if n_finished else float("nan"),
                }
            )
    return pd.DataFrame(rows)


def apply_coverage_rule(
    matches: pd.DataFrame,
    families: Iterable[str] | None = None,
    *,
    min_share: float = MIN_COVERAGE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Blank a statistic in every league-season whose coverage is below ``min_share``.

    Returns ``(matches, table)``.  ``table`` is :func:`coverage_table` plus the
    boolean ``included``.  Nothing is imputed: an excluded league-season simply has
    no value for that statistic (NaN), so the framework never scores or trains on it.
    Auxiliary statistics (fouls, xG, red cards) are not filtered; they are used as
    features only when present.
    """

    names = tuple(families) if families is not None else tuple(FAMILIES)
    table = coverage_table(matches, names)
    table["included"] = table["share"] >= min_share
    out = matches.copy()
    keys = pd.MultiIndex.from_arrays([out["league_code"], out["season"]])
    for name in names:
        excluded = table[(table["family"] == name) & ~table["included"]]
        if excluded.empty:
            continue
        bad = keys.isin(pd.MultiIndex.from_arrays([excluded["league_code"], excluded["season"]]))
        for column in (f"home_{name}", f"away_{name}"):
            out.loc[bad, column] = np.nan
    return out, table


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountOutcome:
    """Picklable outcome function: class index (0 = under, 1 = over) or NaN.

    The outcome is known only when *both* teams' counts are known (see the
    module docstring), so all markets of a family share the same finished matches.
    """

    home: str
    away: str
    scope: Scope
    line: float

    def __call__(self, frame: pd.DataFrame) -> np.ndarray:
        home = pd.to_numeric(frame[self.home], errors="coerce").to_numpy(dtype=float)
        away = pd.to_numeric(frame[self.away], errors="coerce").to_numpy(dtype=float)
        value = {"total": home + away, "home": home, "away": away}[self.scope]
        out = (value > self.line).astype(float)
        out[np.isnan(home) | np.isnan(away)] = np.nan
        return out


def market_name(family: str, scope: Scope, line: float) -> str:
    return f"{family}_{scope}_{line}"


def count_market(family: CountFamily, scope: Scope, line: float) -> Market:
    """Over/under ``line`` for the match total (``scope='total'``) or one team."""

    if scope not in ("total", "home", "away"):
        raise ValueError("scope must be 'total', 'home' or 'away'")
    if line != np.floor(line) + 0.5:
        raise ValueError("count lines must be half-integers (no pushes)")
    return Market(
        market_name(family.name, scope, line),
        ("under", "over"),
        CountOutcome(family.home, family.away, scope, float(line)),
        outcome_columns=family.columns,
    )


def count_markets(family: CountFamily | str) -> list[Market]:
    """All evaluated markets of a family: total lines, then home and away team lines."""

    fam = FAMILIES[family] if isinstance(family, str) else family
    markets = [count_market(fam, "total", line) for line in fam.total_lines]
    for scope in ("home", "away"):
        markets.extend(count_market(fam, scope, line) for line in fam.team_lines)
    return markets


def count_config(base: WalkForwardConfig = COMPARISON_CONFIG) -> WalkForwardConfig:
    """The comparison protocol with every count column hidden from ``predict``.

    Only ``hidden_columns`` differs from ``base``: test seasons, scope, cadence,
    inner selection and latency are the protocol's.
    """

    hidden = tuple(dict.fromkeys((*DEFAULT_HIDDEN_COLUMNS, *base.hidden_columns, *STAT_COLUMNS)))
    return replace(base, hidden_columns=hidden)


def leakage_market(family: CountFamily | str) -> Market:
    """Market for :func:`~model_buk.evaluation.framework.check_no_lookahead`.

    It reads the outcome of the family's first total line but declares *every*
    count column as outcome column, so the leakage test shuffles all statistics
    of the future matches jointly (auxiliary statistics included).
    """

    fam = FAMILIES[family] if isinstance(family, str) else family
    base = count_market(fam, "total", fam.total_lines[0])
    return replace(base, outcome_columns=STAT_COLUMNS)


__all__ = [
    "AUX_STATS",
    "ALL_STATS",
    "CountFamily",
    "CountOutcome",
    "DEFAULT_FAMILIES",
    "FAMILIES",
    "MIN_CARD_FEED_SHARE",
    "MIN_COVERAGE",
    "STAT_COLUMNS",
    "apply_coverage_rule",
    "card_feed_reliability",
    "count_config",
    "count_market",
    "count_markets",
    "coverage_table",
    "leakage_market",
    "load_count_matches",
    "market_name",
]
