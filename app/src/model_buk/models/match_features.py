"""As-of feature store: pre-kickoff features of a match from earlier matches only.

The store holds every finished match of every league (goals, teams, kickoff, and
optionally the per-team match statistics of API-Football's ``team_match_stats``).
It answers one kind of question, always with an explicit cut-off::

    features(league, season, home team, away team, kickoff, cutoff)

The answer is computed from the matches that kicked off strictly *before* the
cut-off and from nothing else.  The cut-off is what the evaluation framework
calls the training cut-off of a matchday (first kickoff of the matchday minus
the result latency), so a feature vector for a forecast contains no result that
the framework would not have shown to a model.  Every lookup goes through
:meth:`AsOfStore._cutoff_rank`, one exact integer comparison against the
sorted kickoff instants; the tests mutation-check that shifting the cut-off by
a day changes the answer.

Layout: each series (a team's matches, a league's matches, a team's pi-rating
history) is a sorted array with the key ``series * M + time_rank``; the matches
of a series before a cut-off are the slice ``[start, pos)`` found with two
binary searches, and windowed means are differences of cumulative sums, so a
whole matchday (or the whole history, for training rows) is featurised with a
handful of vectorised calls.

Features (all pre-kickoff, missing stays missing -- never 0):

- pi-ratings of both teams in this league (:mod:`.pi_ratings`), the expected
  goal difference they imply and how many league matches each rating rests on;
- rolling form of each team over the last 5 and 10 matches (any competition in
  the store): goals, shots, shots on target, corners, xG for and against, and
  points.  A window mean needs at least half of the window to carry a value; a
  statistic that was never recorded (xG before 2022, leagues without shot data)
  stays NaN;
- rest days since the team's previous match and matches in the last 14 days;
- promoted / new-team flag: 1 if the team played no match in this league in the
  previous season, 0 if it did, NaN if the previous season of the league is not
  in the store (nothing to compare with);
- league level over the last 730 days: goals per side, home-win, draw,
  over-2.5 and both-teams-scored rates (NaN below 100 matches).

Injuries and line-ups are not used: the injuries table is retrospective (it
lists who missed the match) and would leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from model_buk.models.pi_ratings import PiRatingConfig, pi_rating_pass

_NS_PER_HOUR = 3_600_000_000_000
_NS_PER_DAY = 24 * _NS_PER_HOUR

FORM_WINDOWS: tuple[int, ...] = (5, 10)
# (feature stem, column of team_match_stats); goals and points come from the match itself.
_STAT_SOURCES: tuple[tuple[str, str], ...] = (
    ("shots", "total_shots"),
    ("sot", "shots_on_goal"),
    ("corners", "corners"),
    ("xg", "expected_goals"),
)
_FORM_STEMS: tuple[str, ...] = (
    "goals_for",
    "goals_against",
    "shots_for",
    "shots_against",
    "sot_for",
    "sot_against",
    "corners_for",
    "corners_against",
    "xg_for",
    "xg_against",
    "points",
)
_LEAGUE_STEMS: tuple[str, ...] = ("home_goals", "away_goals", "home_win", "draw", "over25", "btts")
_MAX_TEAM_ID = 1_000_000
_MAX_SEASON = 10_000
REST_DAYS_CAP = 60.0
LEAGUE_WINDOW_DAYS = 730
MIN_LEAGUE_MATCHES = 100
MIN_PREVIOUS_SEASON_MATCHES = 100
_RECENT_DAYS = 14


def feature_names(windows: Sequence[int] = FORM_WINDOWS) -> tuple[str, ...]:
    """Column order of every feature matrix produced by :class:`AsOfStore`."""
    names = [f"lg_{stem}" for stem in _LEAGUE_STEMS]
    names += ["pi_h_home", "pi_h_away", "pi_a_home", "pi_a_away", "pi_exp_gd", "pi_h_matches", "pi_a_matches"]
    for side in ("h", "a"):
        for k in windows:
            names += [f"{side}_{stem}_{k}" for stem in _FORM_STEMS]
    names += ["h_rest_days", "a_rest_days", "h_matches_14d", "a_matches_14d", "h_promoted", "a_promoted"]
    names += [f"d_goal_diff_{k}" for k in windows]
    names += [f"d_xg_diff_{max(windows)}"]
    return tuple(names)


@dataclass(frozen=True)
class TrainingMatrix:
    """Features of every finished match as of ``kickoff - latency`` next to its outcome."""

    names: tuple[str, ...]
    X: np.ndarray  # float32, NaN = missing
    t_ns: np.ndarray
    fixture_id: np.ndarray
    league_code: np.ndarray
    season: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray

    def __len__(self) -> int:
        return len(self.t_ns)


class AsOfStore:
    """Finished matches indexed for cut-off-bounded feature queries (see the module docstring)."""

    def __init__(
        self,
        matches: pd.DataFrame,
        team_stats: pd.DataFrame | None = None,
        *,
        pi_config: PiRatingConfig | None = None,
        windows: Sequence[int] = FORM_WINDOWS,
        league_window_days: int = LEAGUE_WINDOW_DAYS,
        min_league_matches: int = MIN_LEAGUE_MATCHES,
        min_previous_season_matches: int = MIN_PREVIOUS_SEASON_MATCHES,
        finished_statuses: Sequence[str] = ("FT",),
    ) -> None:
        self.pi_config = pi_config or PiRatingConfig()
        self.windows = tuple(int(k) for k in windows)
        self.league_window_ns = int(league_window_days) * _NS_PER_DAY
        self.min_league_matches = int(min_league_matches)
        self.min_previous_season_matches = int(min_previous_season_matches)
        self.names = feature_names(self.windows)
        self._column = {name: j for j, name in enumerate(self.names)}
        self._build(matches, team_stats, tuple(finished_statuses))

    # ------------------------------------------------------------------ build

    def _build(self, matches: pd.DataFrame, team_stats: pd.DataFrame | None, finished: tuple[str, ...]) -> None:
        required = ("fixture_id", "league_code", "season", "kickoff_utc", "home_team_id", "away_team_id", "home_goals", "away_goals")
        absent = [c for c in required if c not in matches.columns]
        if absent:
            raise ValueError(f"matches is missing required columns: {', '.join(absent)}")
        frame = matches
        if "status_short" in frame.columns:
            frame = frame[frame["status_short"].isin(finished)]
        frame = frame[list(required)].copy()
        for column in ("fixture_id", "season", "home_team_id", "away_team_id", "home_goals", "away_goals"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        stamp = pd.to_datetime(frame["kickoff_utc"], utc=True, format="mixed", errors="coerce")
        valid = frame[["fixture_id", "season", "home_team_id", "away_team_id", "home_goals", "away_goals"]].notna().all(axis=1).to_numpy() & stamp.notna().to_numpy()
        stamp = stamp[valid]
        kickoff = stamp.dt.as_unit("ns").dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().view("int64")
        frame = frame.loc[valid]
        if frame["fixture_id"].duplicated().any():
            raise ValueError("fixture_id must be unique")
        order = np.lexsort((frame["fixture_id"].to_numpy(), kickoff))
        frame = frame.iloc[order].reset_index(drop=True)
        t = kickoff[order]
        n = len(frame)
        self.n_matches = n
        self.t_ns = t
        self.fixture_id = frame["fixture_id"].to_numpy(dtype=np.int64)
        self.league_code = frame["league_code"].astype(str).to_numpy(dtype=object)
        self.season = frame["season"].to_numpy(dtype=np.int64)
        home = frame["home_team_id"].to_numpy(dtype=np.int64)
        away = frame["away_team_id"].to_numpy(dtype=np.int64)
        hg = frame["home_goals"].to_numpy(dtype=float)
        ag = frame["away_goals"].to_numpy(dtype=float)
        self.home_goals = hg
        self.away_goals = ag
        self.home_team_id = home
        self.away_team_id = away
        if n and (max(home.max(), away.max()) >= _MAX_TEAM_ID or self.season.max() >= _MAX_SEASON):
            raise ValueError("team id or season out of the range the composite keys support")

        self._times = np.unique(t)
        self._m = len(self._times) + 2
        time_rank = np.searchsorted(self._times, t)
        m = self._m

        # -- leagues
        self._leagues = pd.Index(np.unique(self.league_code)) if n else pd.Index([])
        league_rank = self._leagues.get_indexer(self.league_code)

        # -- team-match rows (two per match), sorted by (team, time)
        team = np.concatenate([home, away])
        opponent = np.concatenate([away, home])
        fixture2 = np.concatenate([self.fixture_id, self.fixture_id])
        rank2 = np.concatenate([time_rank, time_rank])
        t2 = np.concatenate([t, t])
        goals_for = np.concatenate([hg, ag])
        goals_against = np.concatenate([ag, hg])
        points = np.where(goals_for > goals_against, 3.0, np.where(goals_for == goals_against, 1.0, 0.0))
        columns: dict[str, np.ndarray] = {
            "goals_for": goals_for,
            "goals_against": goals_against,
            "points": points,
        }
        own_stats, opp_stats = self._stat_columns(team_stats, fixture2, team, opponent)
        for stem, _source in _STAT_SOURCES:
            columns[f"{stem}_for"] = own_stats[stem]
            columns[f"{stem}_against"] = opp_stats[stem]
        values = np.column_stack([columns[stem] for stem in _FORM_STEMS])
        self._teams = pd.Index(np.unique(team)) if n else pd.Index([], dtype="int64")
        team_rank = self._teams.get_indexer(team)
        by_team = np.lexsort((fixture2, rank2, team_rank))
        self._team_comp = team_rank[by_team] * m + rank2[by_team]
        self._team_t = t2[by_team]
        sorted_values = values[by_team]
        self._cs_val = np.vstack([np.zeros((1, values.shape[1])), np.cumsum(np.nan_to_num(sorted_values, nan=0.0), axis=0)])
        self._cs_cnt = np.vstack([np.zeros((1, values.shape[1])), np.cumsum(~np.isnan(sorted_values), axis=0)])
        self._team_fixture = fixture2[by_team]

        # -- league series (one entry per match)
        by_league = np.lexsort((self.fixture_id, time_rank, league_rank))
        self._league_comp = league_rank[by_league] * m + time_rank[by_league]
        indicators = np.column_stack(
            [
                hg,
                ag,
                (hg > ag).astype(float),
                (hg == ag).astype(float),
                (hg + ag > 2.5).astype(float),
                ((hg > 0) & (ag > 0)).astype(float),
            ]
        )[by_league]
        self._lg_cs = np.vstack([np.zeros((1, indicators.shape[1])), np.cumsum(indicators, axis=0)])

        # -- pi-ratings: one chronological pass per (league, team); state after each match
        lt_home = league_rank * _MAX_TEAM_ID + home
        lt_away = league_rank * _MAX_TEAM_ID + away
        self._lt_keys = np.unique(np.concatenate([lt_home, lt_away])) if n else np.zeros(0, dtype=np.int64)
        home_key = np.searchsorted(self._lt_keys, lt_home)
        away_key = np.searchsorted(self._lt_keys, lt_away)
        passed = pi_rating_pass(home_key, away_key, hg - ag, len(self._lt_keys), self.pi_config)
        entry_key = np.concatenate([home_key, away_key])
        entry_rank = np.concatenate([time_rank, time_rank])
        entry_fixture = np.concatenate([self.fixture_id, self.fixture_id])
        by_pi = np.lexsort((entry_fixture, entry_rank, entry_key))
        self._pi_comp = entry_key[by_pi] * m + entry_rank[by_pi]
        self._pi_home = np.concatenate([passed.home_home, passed.away_home])[by_pi]
        self._pi_away = np.concatenate([passed.home_away, passed.away_away])[by_pi]
        self._pi_n = np.concatenate([passed.home_matches, passed.away_matches])[by_pi].astype(float)

        # -- previous-season presence: per (league, team, season) and per (league, season)
        team_season_key = np.concatenate([lt_home, lt_away]) * _MAX_SEASON + np.concatenate([self.season, self.season])
        order_ts = np.lexsort((entry_fixture, entry_rank, team_season_key))
        self._ts_comp = team_season_key[order_ts] * m + entry_rank[order_ts]
        league_season_key = league_rank * _MAX_SEASON + self.season
        order_ls = np.lexsort((self.fixture_id, time_rank, league_season_key))
        self._ls_comp = league_season_key[order_ls] * m + time_rank[order_ls]

    @staticmethod
    def _stat_columns(
        team_stats: pd.DataFrame | None, fixture2: np.ndarray, team: np.ndarray, opponent: np.ndarray
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """Own and opponent statistic per team-match row; NaN where the statistic was not recorded."""
        n = len(fixture2)
        own = {stem: np.full(n, np.nan) for stem, _ in _STAT_SOURCES}
        opp = {stem: np.full(n, np.nan) for stem, _ in _STAT_SOURCES}
        if team_stats is None or len(team_stats) == 0:
            return own, opp
        stats = team_stats.drop_duplicates(["fixture_id", "team_id"])
        index = pd.MultiIndex.from_arrays([stats["fixture_id"].to_numpy(dtype=np.int64), stats["team_id"].to_numpy(dtype=np.int64)])
        own_pos = index.get_indexer(pd.MultiIndex.from_arrays([fixture2, team]))
        opp_pos = index.get_indexer(pd.MultiIndex.from_arrays([fixture2, opponent]))
        for stem, source in _STAT_SOURCES:
            if source not in stats.columns:
                continue
            column = pd.to_numeric(stats[source], errors="coerce").to_numpy(dtype=float)
            own[stem] = np.where(own_pos >= 0, column[np.where(own_pos >= 0, own_pos, 0)], np.nan)
            opp[stem] = np.where(opp_pos >= 0, column[np.where(opp_pos >= 0, opp_pos, 0)], np.nan)
        return own, opp

    # ---------------------------------------------------------------- lookups

    def _cutoff_rank(self, cutoff_ns: np.ndarray | int) -> np.ndarray:
        """Number of distinct kickoff instants strictly before the cut-off.

        A series entry with time rank ``r`` counts as known at the cut-off iff
        ``r < _cutoff_rank(cutoff)``, which is exactly ``kickoff < cutoff``.  Every
        as-of lookup of the store goes through this one method.
        """
        return np.searchsorted(self._times, np.asarray(cutoff_ns, dtype=np.int64), side="left")

    def _team_bounds(self, team_id: np.ndarray, q_rank: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Slice ``[start, pos)`` of each team's matches known at the cut-off, plus a "team exists" mask."""
        rank = self._teams.get_indexer(team_id)
        known = rank >= 0
        base = np.where(known, rank, 0) * self._m
        start = np.searchsorted(self._team_comp, base, side="left")
        pos = np.searchsorted(self._team_comp, base + q_rank, side="left")
        return np.where(known, start, 0), np.where(known, pos, 0), known

    def team_history_fixtures(self, team_id: int, cutoff_ns: int) -> np.ndarray:
        """Fixture ids of the matches of ``team_id`` that count as known at ``cutoff_ns`` (all competitions)."""
        q = self._cutoff_rank(np.array([cutoff_ns]))
        start, pos, _ = self._team_bounds(np.array([team_id], dtype=np.int64), q)
        return self._team_fixture[start[0] : pos[0]]

    def _lt_rank(self, league_rank: np.ndarray, team_id: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        key = np.where(league_rank >= 0, league_rank, 0) * _MAX_TEAM_ID + team_id
        rank = np.searchsorted(self._lt_keys, key)
        clipped = np.minimum(rank, max(len(self._lt_keys) - 1, 0))
        known = (league_rank >= 0) & (len(self._lt_keys) > 0)
        if len(self._lt_keys):
            known &= self._lt_keys[clipped] == key
        return np.where(known, clipped, 0), known

    def _pi_state(self, lt_rank: np.ndarray, known: np.ndarray, q_rank: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        base = lt_rank * self._m
        start = np.searchsorted(self._pi_comp, base, side="left")
        pos = np.searchsorted(self._pi_comp, base + q_rank, side="left")
        has = known & (pos > start)
        last = np.where(has, pos - 1, 0)
        if len(self._pi_home) == 0:
            zeros = np.zeros(len(q_rank))
            return zeros, zeros.copy(), zeros.copy()
        home = np.where(has, self._pi_home[last], 0.0)
        away = np.where(has, self._pi_away[last], 0.0)
        played = np.where(has, self._pi_n[last], 0.0)
        return home, away, played

    def _count_in_series(self, comp: np.ndarray, key: np.ndarray, q_rank: np.ndarray, valid: np.ndarray) -> np.ndarray:
        base = key * self._m
        count = np.searchsorted(comp, base + q_rank, side="left") - np.searchsorted(comp, base, side="left")
        return np.where(valid, count, 0)

    def _form(self, team_id: np.ndarray, q_rank: np.ndarray, kickoff_ns: np.ndarray, out: np.ndarray, side: str) -> None:
        start, pos, known = self._team_bounds(team_id, q_rank)
        for k in self.windows:
            lo = np.maximum(pos - k, start)
            count = self._cs_cnt[pos] - self._cs_cnt[lo]
            total = self._cs_val[pos] - self._cs_val[lo]
            need = -(-k // 2)
            mean = np.where(count >= need, total / np.maximum(count, 1.0), np.nan)
            for j, stem in enumerate(_FORM_STEMS):
                out[:, self._column[f"{side}_{stem}_{k}"]] = mean[:, j]
        has = pos > start
        previous = self._team_t[np.where(has, pos - 1, 0)] if len(self._team_t) else np.zeros(len(pos), dtype=np.int64)
        rest = (kickoff_ns - previous) / _NS_PER_DAY
        out[:, self._column[f"{side}_rest_days"]] = np.where(has, np.minimum(rest, REST_DAYS_CAP), np.nan)
        recent_lo = self._cutoff_rank(kickoff_ns - _RECENT_DAYS * _NS_PER_DAY)
        base = np.where(known, self._teams.get_indexer(team_id), 0) * self._m
        in_window = pos - np.searchsorted(self._team_comp, base + recent_lo, side="left")
        out[:, self._column[f"{side}_matches_14d"]] = np.where(known, np.clip(in_window, 0, None), 0.0)

    # --------------------------------------------------------------- features

    def features(
        self,
        *,
        league_code: np.ndarray,
        season: np.ndarray,
        home_team_id: np.ndarray,
        away_team_id: np.ndarray,
        kickoff_ns: np.ndarray,
        cutoff_ns: np.ndarray | int,
    ) -> np.ndarray:
        """Feature matrix ``(n, len(names))`` for matches to forecast, from matches before ``cutoff_ns`` only.

        ``cutoff_ns`` is one instant or one per row.  ``kickoff_ns`` of the
        forecast match only enters through rest days and the 14-day count (both
        measured back from its kickoff, never past the cut-off).
        """
        league_code = np.asarray(league_code, dtype=object)
        n = len(league_code)
        season = np.asarray(season, dtype=np.int64)
        home = np.asarray(home_team_id, dtype=np.int64)
        away = np.asarray(away_team_id, dtype=np.int64)
        kickoff = np.asarray(kickoff_ns, dtype=np.int64)
        cutoff = np.broadcast_to(np.asarray(cutoff_ns, dtype=np.int64), (n,))
        out = np.full((n, len(self.names)), np.nan)
        if n == 0:
            return out
        q = self._cutoff_rank(cutoff)
        league_rank = self._leagues.get_indexer(league_code) if len(self._leagues) else np.full(n, -1)
        league_known = league_rank >= 0

        # league level
        base = np.where(league_known, league_rank, 0) * self._m
        hi = np.searchsorted(self._league_comp, base + q, side="left")
        lo = np.searchsorted(self._league_comp, base + self._cutoff_rank(cutoff - self.league_window_ns), side="left")
        lo = np.minimum(lo, hi)
        count = hi - lo
        sums = self._lg_cs[hi] - self._lg_cs[lo]
        enough = league_known & (count >= self.min_league_matches)
        means = np.where(enough[:, None], sums / np.maximum(count, 1)[:, None], np.nan)
        for j, stem in enumerate(_LEAGUE_STEMS):
            out[:, self._column[f"lg_{stem}"]] = means[:, j]

        # pi-ratings in this league
        lt_home, home_known = self._lt_rank(league_rank, home)
        lt_away, away_known = self._lt_rank(league_rank, away)
        h_home, h_away, h_n = self._pi_state(lt_home, home_known, q)
        a_home, a_away, a_n = self._pi_state(lt_away, away_known, q)
        out[:, self._column["pi_h_home"]] = h_home
        out[:, self._column["pi_h_away"]] = h_away
        out[:, self._column["pi_a_home"]] = a_home
        out[:, self._column["pi_a_away"]] = a_away
        cfg = self.pi_config

        def to_goals(rating: np.ndarray) -> np.ndarray:
            magnitude = cfg.base ** (np.abs(rating) / cfg.scale) - 1.0
            return np.where(rating >= 0.0, magnitude, -magnitude)

        out[:, self._column["pi_exp_gd"]] = to_goals(h_home) - to_goals(a_away)
        out[:, self._column["pi_h_matches"]] = h_n
        out[:, self._column["pi_a_matches"]] = a_n

        # form, rest, congestion
        self._form(home, q, kickoff, out, "h")
        self._form(away, q, kickoff, out, "a")

        # promoted / new to the league: no match in this league last season
        previous = season - 1
        reference = self._count_in_series(self._ls_comp, np.where(league_known, league_rank, 0) * _MAX_SEASON + previous, q, league_known)
        comparable = league_known & (reference >= self.min_previous_season_matches)
        for side, team in (("h", home), ("a", away)):
            key = (np.where(league_known, league_rank, 0) * _MAX_TEAM_ID + team) * _MAX_SEASON + previous
            played = self._count_in_series(self._ts_comp, key, q, league_known)
            out[:, self._column[f"{side}_promoted"]] = np.where(comparable, (played == 0).astype(float), np.nan)

        # differences of form
        for k in self.windows:
            h = out[:, self._column[f"h_goals_for_{k}"]] - out[:, self._column[f"h_goals_against_{k}"]]
            a = out[:, self._column[f"a_goals_for_{k}"]] - out[:, self._column[f"a_goals_against_{k}"]]
            out[:, self._column[f"d_goal_diff_{k}"]] = h - a
        k = max(self.windows)
        h = out[:, self._column[f"h_xg_for_{k}"]] - out[:, self._column[f"h_xg_against_{k}"]]
        a = out[:, self._column[f"a_xg_for_{k}"]] - out[:, self._column[f"a_xg_against_{k}"]]
        out[:, self._column[f"d_xg_diff_{k}"]] = h - a
        return out

    def training_matrix(self, latency_ns: int) -> TrainingMatrix:
        """Features of every finished match as of ``kickoff - latency`` (what a forecaster knew 3 h before it)."""
        latency = int(latency_ns)
        x = self.features(
            league_code=self.league_code,
            season=self.season,
            home_team_id=self.home_team_id,
            away_team_id=self.away_team_id,
            kickoff_ns=self.t_ns,
            cutoff_ns=self.t_ns - latency,
        )
        return TrainingMatrix(
            names=self.names,
            X=x.astype(np.float32),
            t_ns=self.t_ns,
            fixture_id=self.fixture_id,
            league_code=self.league_code,
            season=self.season,
            home_goals=self.home_goals,
            away_goals=self.away_goals,
        )


__all__ = ["AsOfStore", "FORM_WINDOWS", "TrainingMatrix", "feature_names"]
