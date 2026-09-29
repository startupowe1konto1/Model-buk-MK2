from __future__ import annotations

import sqlite3
import zlib
from contextlib import closing
from zipfile import ZipFile

import pandas as pd
import pytest

from model_buk.ingestion.fd_af_join import (
    API_COLUMNS,
    FD_IDENTITY_COLUMNS,
    AliasRow,
    TeamResolver,
    assign_league_ids,
    classify_odds_column,
    join_matches,
    load_aliases,
    normalize_team_name,
    odds_columns_of,
    read_football_data_archives,
    render_report,
    save_aliases,
    seed_team_aliases,
    summarize_by_league_season,
    write_join_database,
)

MEMBER_MAIN = "football_data/football-data-uk/main/"
MEMBER_EXTRA = "football_data/football-data-uk/extra/"


def api_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "league_id": 39,
        "league_code": "EPL",
        "season": 2024,
        "kickoff_utc": "2024-08-16T19:00:00+00:00",
        "status_short": "FT",
    }
    frame = pd.DataFrame([{**defaults, **row} for row in rows])
    return frame.reindex(columns=list(API_COLUMNS))


def fd_frame(rows: list[dict], odds: list[str] | None = None) -> pd.DataFrame:
    defaults = {
        "fd_div": "E0",
        "fd_league_name": None,
        "fd_season": "2024/2025",
        "fd_season_start": 2024,
        "fd_date": "2024-08-16",
        "fd_time": None,
        "fd_ht_home_goals": None,
        "fd_ht_away_goals": None,
    }
    frame = pd.DataFrame([{**defaults, **row} for row in rows])
    frame["fd_date"] = pd.to_datetime(frame["fd_date"])
    columns = list(FD_IDENTITY_COLUMNS) + list(odds or [])
    return frame.reindex(columns=columns)


def fx(fixture_id, home, away, hg, ag, *, home_id=None, away_id=None, day="2024-08-16", **extra):
    return {
        "fixture_id": fixture_id,
        "home_team": home,
        "away_team": away,
        "home_team_id": home_id if home_id is not None else zlib.crc32(home.encode()) % 100_000,
        "away_team_id": away_id if away_id is not None else zlib.crc32(away.encode()) % 100_000,
        "home_goals": hg,
        "away_goals": ag,
        "kickoff_utc": f"{day}T19:00:00+00:00",
        **extra,
    }


def fd(row_id, home, away, hg, ag, *, day="2024-08-16", **extra):
    return {
        "source_row_id": row_id,
        "fd_home_team": home,
        "fd_away_team": away,
        "fd_home_goals": hg,
        "fd_away_goals": ag,
        "fd_date": day,
        **extra,
    }


def resolver_for(api: pd.DataFrame, aliases: list[AliasRow] | None = None) -> TeamResolver:
    return TeamResolver(api, aliases or [])


# --- name normalisation -------------------------------------------------------------


def test_normalize_team_name_folds_accents_club_tokens_and_special_letters():
    assert normalize_team_name("Brøndby IF") == normalize_team_name("Brondby")
    assert normalize_team_name("1. FC Köln") == normalize_team_name("Koln")
    assert normalize_team_name("Nott'm Forest") == "nott m forest"
    assert normalize_team_name("Atlético  Madrid") == "atletico madrid"
    assert normalize_team_name("Łódź") == "lodz"
    assert normalize_team_name("  ") == ""


# --- joining ------------------------------------------------------------------------


def test_known_pairs_join_and_carry_both_sides_and_odds():
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Everton", "Fulham", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
        ]
    )
    fd_rows = fd_frame(
        [
            fd("a", "Arsenal", "Chelsea", 2, 1, PSH=1.9, PSCH=1.8),
            fd("b", "Everton", "Fulham", 0, 0, day="2024-08-17", PSH=2.4, PSCH=None),
        ],
        odds=["PSH", "PSCH"],
    )

    result = join_matches(fd_rows, api, resolver_for(api))

    assert result.unmatched.empty
    matched = result.matched.set_index("fd_source_row_id")
    assert matched.loc["a", "fixture_id"] == 1
    assert matched.loc["b", "fixture_id"] == 2
    assert matched.loc["a", "af_home_team"] == "Arsenal"
    assert matched.loc["a", "fd_home_goals"] == 2 and matched.loc["a", "af_home_goals"] == 2
    assert matched.loc["a", "date_diff_days"] == 0
    assert matched.loc["a", "PSH"] == pytest.approx(1.9)
    assert matched.loc["a", "PSCH"] == pytest.approx(1.8)
    # A missing price stays missing; it is never turned into zero.
    assert pd.isna(matched.loc["b", "PSCH"])


@pytest.mark.parametrize("offset,expected_diff", [(-1, -1), (0, 0), (1, 1)])
def test_date_window_is_plus_minus_one_day(offset, expected_diff):
    api = api_frame([fx(1, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-16")])
    fd_day = (pd.Timestamp("2024-08-16") + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 0, day=fd_day)]), api, resolver_for(api))

    assert len(result.matched) == 1
    assert result.matched.iloc[0]["date_diff_days"] == expected_diff


def test_two_days_apart_is_no_candidate_and_reported_with_nearest_date():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-16")])
    result = join_matches(
        fd_frame([fd("a", "Arsenal", "Chelsea", 1, 0, day="2024-08-18")]), api, resolver_for(api)
    )

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "no_candidate_on_date"
    assert "2024-08-16" in row["detail"]


def test_score_mismatch_rejects_the_pair_and_is_reported():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 2)]), api, resolver_for(api))

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "score_mismatch"
    assert "1:2" in row["detail"] and "2:1" in row["detail"]
    assert bool(row["in_scope"])


@pytest.mark.parametrize(
    "fd_score,expected_basis",
    [((2, 1), "api_goals"), ((1, 1), "api_regulation_time")],
)
def test_after_extra_time_either_the_final_or_the_90_minute_score_may_agree(fd_score, expected_basis):
    api = api_frame(
        [
            fx(
                1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2,
                status_short="AET", et_home_goals=1, et_away_goals=0,
            )
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", *fd_score)]), api, resolver_for(api))

    assert result.unmatched.empty
    assert result.matched.iloc[0]["score_basis"] == expected_basis


def test_extra_time_goals_are_ignored_for_matches_decided_in_regular_time():
    api = api_frame(
        [
            fx(
                1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2,
                status_short="FT", et_home_goals=1, et_away_goals=0,
            )
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 1)]), api, resolver_for(api))

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "score_mismatch"


def test_after_extra_time_a_score_that_fits_neither_reading_is_still_rejected():
    api = api_frame(
        [
            fx(
                1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2,
                status_short="PEN", et_home_goals=1, et_away_goals=0,
            )
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 0, 0)]), api, resolver_for(api))

    row = result.unmatched.iloc[0]
    assert row["reason"] == "score_mismatch"
    assert "1:1" in row["detail"]  # the 90-minute reading is shown next to the final score


def test_missing_league_mapping_is_excluded_with_reason():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    rows = fd_frame([fd("a", "Arsenal", "Chelsea", 2, 1, fd_div="ZZ9")])

    result = join_matches(rows, api, resolver_for(api))

    row = result.unmatched.iloc[0]
    assert row["reason"] == "no_league_mapping"
    assert not bool(row["in_scope"])


def test_season_not_covered_by_api_is_excluded_not_counted_as_failure():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    rows = fd_frame([fd("a", "Arsenal", "Chelsea", 2, 1, fd_season_start=2014, day="2015-01-10")])

    result = join_matches(rows, api, resolver_for(api))

    row = result.unmatched.iloc[0]
    assert row["reason"] == "season_not_covered"
    assert not bool(row["in_scope"])


def test_missing_football_data_score_is_excluded():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", None, None)]), api, resolver_for(api))

    row = result.unmatched.iloc[0]
    assert row["reason"] == "no_fd_score"
    assert not bool(row["in_scope"])


def test_missing_team_alias_is_reported_naming_the_unresolved_side():
    api = api_frame([fx(1, "Manchester City", "Chelsea", 2, 1, home_id=1, away_id=2)])
    result = join_matches(fd_frame([fd("a", "Man City", "Chelsea", 2, 1)]), api, resolver_for(api))

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "team_alias_missing"
    assert "Man City" in row["detail"]
    assert bool(row["in_scope"])


def test_manual_alias_beats_block_and_block_beats_auto_for_the_same_name():
    api = api_frame(
        [
            fx(1, "Manchester City", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Arsenal", "Everton", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
        ]
    )
    auto = AliasRow(39, "E0", "Man City", 3, "Arsenal", "auto", 5)
    block = AliasRow(39, "E0", "Man City", None, "", "block")
    manual = AliasRow(39, "E0", "Man City", 1, "Manchester City", "manual", None, "checked")

    assert resolver_for(api, [auto]).resolve(39, "Man City") == (3, "auto")
    assert resolver_for(api, [auto, block]).resolve(39, "Man City") == (None, None)
    assert resolver_for(api, [block, auto]).resolve(39, "Man City") == (None, None)
    assert resolver_for(api, [auto, manual]).resolve(39, "Man City") == (1, "manual")
    assert resolver_for(api, [manual, auto]).resolve(39, "Man City") == (1, "manual")


def test_exact_name_shared_by_two_api_teams_is_never_guessed():
    # "Lyon" and "FC Lyon" fold to the same key but are different API teams: ambiguous stays unresolved.
    api = api_frame(
        [
            fx(1, "Lyon", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "FC Lyon", "Everton", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
        ]
    )
    resolver = resolver_for(api)

    assert resolver.resolve(39, "Lyon") == (None, None)
    assert resolver.resolve(39, "Chelsea") == (2, "exact")
    result = join_matches(fd_frame([fd("a", "Lyon", "Chelsea", 2, 1)]), api, resolver)
    assert result.matched.empty
    assert result.unmatched.iloc[0]["reason"] == "team_alias_missing"


def test_a_name_made_only_of_club_tokens_resolves_to_nothing():
    api = api_frame([fx(1, "FC", "Chelsea", 2, 1, home_id=1, away_id=2)])

    assert normalize_team_name("FC") == ""
    assert resolver_for(api).resolve(39, "AS") == (None, None)


def test_alias_row_resolves_the_name_and_manual_alias_beats_exact_match():
    api = api_frame(
        [
            fx(1, "Manchester City", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Arsenal", "Everton", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
        ]
    )
    aliases = [AliasRow(39, "E0", "Man City", 1, "Manchester City", "auto", 5)]
    result = join_matches(fd_frame([fd("a", "Man City", "Chelsea", 2, 1)]), api, resolver_for(api, aliases))

    assert result.matched.iloc[0]["fixture_id"] == 1
    assert result.matched.iloc[0]["home_match_method"] == "auto"
    assert result.matched.iloc[0]["away_match_method"] == "exact"

    # A human decision overrides even a name that would match exactly.
    override = [AliasRow(39, "E0", "Arsenal", 4, "Everton", "manual", None, "test")]
    resolver = resolver_for(api, override)
    assert resolver.resolve(39, "Arsenal") == (4, "manual")


def test_blocked_alias_stops_automatic_resolution():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    blocked = [AliasRow(39, "E0", "Arsenal", None, "", "block", None, "do not map")]
    assert resolver_for(api, blocked).resolve(39, "Arsenal") == (None, None)
    assert resolver_for(api).resolve(39, "Arsenal") == (1, "exact")


def test_ambiguous_candidates_stay_unmatched():
    # Two fixtures of the same pairing with the same score inside the +/-1 day window.
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-15"),
            fx(2, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-17"),
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 0)]), api, resolver_for(api))

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "ambiguous"
    assert "1" in row["detail"] and "2" in row["detail"]


def test_exact_date_candidate_wins_over_a_neighbouring_day_only_when_unique():
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-16"),
            fx(2, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, day="2024-08-17"),
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 0)]), api, resolver_for(api))

    assert len(result.matched) == 1
    assert result.matched.iloc[0]["fixture_id"] == 1


def test_identical_duplicate_football_data_rows_join_only_once():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    rows = fd_frame([fd("a", "Arsenal", "Chelsea", 2, 1), fd("b", "Arsenal", "Chelsea", 2, 1)])

    result = join_matches(rows, api, resolver_for(api))

    assert list(result.matched["fd_source_row_id"]) == ["a"]
    dup = result.unmatched.iloc[0]
    assert dup["fd_source_row_id"] == "b"
    assert dup["reason"] == "duplicate_fd_row"
    assert not bool(dup["in_scope"])


def test_conflicting_claims_on_one_fixture_reject_both_rows():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    # Same fixture claimed on different days by two football-data rows.
    rows = fd_frame(
        [
            fd("a", "Arsenal", "Chelsea", 2, 1, day="2024-08-16"),
            fd("b", "Arsenal", "Chelsea", 2, 1, day="2024-08-17"),
        ]
    )

    result = join_matches(rows, api, resolver_for(api))

    assert result.matched.empty
    assert set(result.unmatched["reason"]) == {"ambiguous"}
    assert set(result.unmatched["fd_source_row_id"]) == {"a", "b"}


def test_one_to_one_holds_on_a_full_season_style_input():
    teams = {i: f"Team {i}" for i in range(1, 7)}
    api_rows, fd_rows = [], []
    fixture_id = 0
    for home in teams:
        for away in teams:
            if home == away:
                continue
            fixture_id += 1
            day = (pd.Timestamp("2024-08-01") + pd.Timedelta(days=fixture_id * 4)).strftime("%Y-%m-%d")
            api_rows.append(
                fx(fixture_id, teams[home], teams[away], fixture_id % 4, fixture_id % 3, home_id=home, away_id=away, day=day)
            )
            fd_rows.append(
                fd(f"r{fixture_id}", teams[home], teams[away], fixture_id % 4, fixture_id % 3, day=day)
            )
    api = api_frame(api_rows)
    result = join_matches(fd_frame(fd_rows), api, resolver_for(api))

    assert len(result.matched) == fixture_id
    assert result.matched["fixture_id"].is_unique
    assert result.matched["fd_source_row_id"].is_unique
    assert result.api_unpaired.empty


def test_swapped_home_away_is_reported_not_joined():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    result = join_matches(fd_frame([fd("a", "Chelsea", "Arsenal", 1, 2)]), api, resolver_for(api))

    assert result.matched.empty
    row = result.unmatched.iloc[0]
    assert row["reason"] == "home_away_swapped"
    assert bool(row["in_scope"])


def test_api_fixture_without_result_is_reported():
    api = api_frame([fx(1, "Arsenal", "Chelsea", None, None, home_id=1, away_id=2, status_short="PST")])
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 1, 0)]), api, resolver_for(api))

    row = result.unmatched.iloc[0]
    assert row["reason"] == "api_no_result"


def test_api_fixtures_without_a_football_data_pair_are_listed():
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Everton", "Fulham", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
            fx(3, "Everton", "Fulham", 1, 1, home_id=3, away_id=4, day="2025-08-17", season=2025),
        ]
    )
    result = join_matches(fd_frame([fd("a", "Arsenal", "Chelsea", 2, 1)]), api, resolver_for(api))

    # Fixture 3 belongs to a season football-data does not cover, so it is not a gap.
    assert list(result.api_unpaired["fixture_id"]) == [2]


def test_every_football_data_row_lands_in_exactly_one_output_table():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    rows = fd_frame(
        [
            fd("a", "Arsenal", "Chelsea", 2, 1),
            fd("b", "Arsenal", "Chelsea", 0, 3, day="2024-08-30"),
            fd("c", "Nobody", "Chelsea", 0, 3),
            fd("d", "Arsenal", "Chelsea", 2, 1, fd_div="ZZ9"),
        ]
    )

    result = join_matches(rows, api, resolver_for(api))

    seen = list(result.matched["fd_source_row_id"]) + list(result.unmatched["fd_source_row_id"])
    assert sorted(seen) == ["a", "b", "c", "d"]


# --- alias seeding and the alias file ----------------------------------------------------


def double_round_robin(api_names: dict[int, str], fd_names: dict[int, str]):
    api_rows, fd_rows = [], []
    fixture_id = 0
    for home in api_names:
        for away in api_names:
            if home == away:
                continue
            fixture_id += 1
            day = (pd.Timestamp("2024-08-01") + pd.Timedelta(days=fixture_id * 3)).strftime("%Y-%m-%d")
            api_rows.append(
                fx(fixture_id, api_names[home], api_names[away], fixture_id % 4, (fixture_id // 2) % 3, home_id=home, away_id=away, day=day)
            )
            fd_rows.append(
                fd(f"r{fixture_id}", fd_names[home], fd_names[away], fixture_id % 4, (fixture_id // 2) % 3, day=day)
            )
    return api_frame(api_rows), fd_frame(fd_rows)


def test_seed_learns_aliases_from_cooccurrence_and_leaves_exact_names_alone():
    api_names = {1: "Manchester City", 2: "Manchester United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    fd_names = {1: "Man City", 2: "Man United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    api, rows = double_round_robin(api_names, fd_names)

    seeded = seed_team_aliases(rows, api, [])

    by_name = {alias.fd_team: alias for alias in seeded}
    assert set(by_name) == {"Man City", "Man United"}
    assert by_name["Man City"].af_team_id == 1
    assert by_name["Man City"].af_team == "Manchester City"
    assert by_name["Man City"].source == "auto"
    assert by_name["Man City"].support >= 2

    result = join_matches(rows, api, TeamResolver(api, seeded))
    assert result.unmatched.empty
    assert len(result.matched) == len(rows)


def test_seed_propagates_when_both_names_of_a_pair_differ():
    api_names = {1: "Manchester City", 2: "Manchester United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    fd_names = {1: "Man City", 2: "Man United", 3: "Gunners", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    api, rows = double_round_robin(api_names, fd_names)

    seeded = seed_team_aliases(rows, api, [])

    assert {alias.fd_team: alias.af_team_id for alias in seeded} == {"Man City": 1, "Man United": 2, "Gunners": 3}


def test_seed_never_accepts_contradictory_evidence():
    api = api_frame(
        [
            # "Mystery FC" plays Arsenal at home; the API says the home side was id 3 twice and id 4 twice.
            fx(1, "Alpha", "Arsenal", 1, 0, home_id=3, away_id=1, day="2024-08-01"),
            fx(2, "Alpha", "Arsenal", 2, 0, home_id=3, away_id=1, day="2024-09-01"),
            fx(3, "Beta", "Arsenal", 3, 0, home_id=4, away_id=1, day="2024-10-01"),
            fx(4, "Beta", "Arsenal", 4, 0, home_id=4, away_id=1, day="2024-11-01"),
        ]
    )
    rows = fd_frame(
        [
            fd("a", "Mystery FC", "Arsenal", 1, 0, day="2024-08-01"),
            fd("b", "Mystery FC", "Arsenal", 2, 0, day="2024-09-01"),
            fd("c", "Mystery FC", "Arsenal", 3, 0, day="2024-10-01"),
            fd("d", "Mystery FC", "Arsenal", 4, 0, day="2024-11-01"),
        ]
    )

    assert seed_team_aliases(rows, api, []) == []


def test_seed_needs_more_than_a_single_coincidence():
    api = api_frame(
        [
            fx(1, "Alpha", "Arsenal", 1, 0, home_id=3, away_id=1, day="2024-08-01"),
        ]
    )
    rows = fd_frame([fd("a", "Mystery FC", "Arsenal", 1, 0, day="2024-08-01")])

    assert seed_team_aliases(rows, api, []) == []


def test_seed_respects_manual_and_blocked_rows():
    api_names = {1: "Manchester City", 2: "Manchester United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    fd_names = {1: "Man City", 2: "Man United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    api, rows = double_round_robin(api_names, fd_names)
    fixed = [
        AliasRow(39, "E0", "Man City", 1, "Manchester City", "manual", None, "checked by hand"),
        AliasRow(39, "E0", "Man United", None, "", "block", None, "never auto-map"),
    ]

    seeded = seed_team_aliases(rows, api, fixed)

    assert seeded == []  # City is fixed, United is blocked, nothing else to learn


def test_alias_file_round_trip_keeps_comments_out_and_sorts(tmp_path):
    path = tmp_path / "aliases.csv"
    rows = [
        AliasRow(140, "SP1", "Ath Madrid", 530, "Atletico Madrid", "auto", 12, ""),
        AliasRow(39, "E0", "Man City", 50, "Manchester City", "manual", None, "hand, checked"),
        AliasRow(39, "E0", "Mystery", None, "", "block", None, "do not map"),
    ]

    save_aliases(path, rows)
    loaded = load_aliases(path)

    assert [a.fd_team for a in loaded] == ["Man City", "Mystery", "Ath Madrid"]
    assert loaded[0].note == "hand, checked"
    assert loaded[1].af_team_id is None and loaded[1].source == "block"
    assert loaded[2].support == 12
    text = path.read_text(encoding="utf-8-sig")
    assert text.startswith("#")


def test_alias_file_missing_or_with_bad_rows(tmp_path):
    assert load_aliases(tmp_path / "missing.csv") == []
    bad = tmp_path / "bad.csv"
    bad.write_text("league_id,fd_div,fd_team,af_team_id,af_team,source,support,note\n39,E0,X,notanumber,Y,manual,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="af_team_id"):
        load_aliases(bad)
    dup = tmp_path / "dup.csv"
    dup.write_text(
        "league_id,fd_div,fd_team,af_team_id,af_team,source,support,note\n"
        "39,E0,X,1,Y,manual,,\n39,E0,X,2,Z,manual,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_aliases(dup)


# --- reading football-data ---------------------------------------------------------------


def make_zip(path, members: dict[str, str]):
    with ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def test_read_archives_handles_main_and_extra_layouts(tmp_path):
    main_csv = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,PSH,PSD,PSA,B365CH,Unnamed: 9\n"
        "E0,08/08/15,Man United,Tottenham,1,0,0,0,1.9,3.5,4.2,1.85,\n"
        "E0,09/08/2015,Leicester,Sunderland,4,2,2,1,1.6,3.9,6.0,,\n"
        ",,,,,,,,,,,,\n"
    )
    extra_csv = (
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA\n"
        "Switzerland,Super League,2016/2017,16/07/2016,17:00,Basel,Young Boys,3,1,H,1.5,4.0,6.0\n"
        "Switzerland,Challenge League,2016/2017,17/07/2016,15:00,Aarau,Wil,0,0,D,2.0,3.2,3.5\n"
    )
    sweden_csv = (
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA\n"
        "Sweden,Allsvenskan ,2017,01/04/2017,15:00,Malmo FF,Hammarby,2,0,H,1.4,4.5,7.0\n"
    )
    archive = make_zip(
        tmp_path / "fd.zip",
        {
            MEMBER_MAIN + "E0_1516.csv": main_csv,
            MEMBER_EXTRA + "SWZ.csv": extra_csv,
            MEMBER_EXTRA + "SWE.csv": sweden_csv,
            "football_data/understat/eng_schedule.csv": "a,b\n1,2\n",
        },
    )

    frame = read_football_data_archives([archive])

    assert list(frame.columns[: len(FD_IDENTITY_COLUMNS)]) == list(FD_IDENTITY_COLUMNS)
    assert len(frame) == 5  # the blank line and the unrelated member are ignored
    e0 = frame[frame["fd_div"] == "E0"].reset_index(drop=True)
    assert list(e0["fd_home_team"]) == ["Man United", "Leicester"]
    assert list(e0["fd_date"].dt.strftime("%Y-%m-%d")) == ["2015-08-08", "2015-08-09"]
    assert set(e0["fd_season"]) == {"2015/2016"} and set(e0["fd_season_start"]) == {2015}
    assert e0.loc[0, "fd_home_goals"] == 1 and e0.loc[1, "fd_away_goals"] == 2
    assert e0.loc[1, "fd_ht_home_goals"] == 2
    swz = frame[frame["fd_div"] == "SWZ"].reset_index(drop=True)
    assert list(swz["fd_league_name"]) == ["Super League", "Challenge League"]
    assert set(swz["fd_season_start"]) == {2016}
    assert swz.loc[0, "fd_time"] == "17:00"
    swe = frame[frame["fd_div"] == "SWE"].reset_index(drop=True)
    assert swe.loc[0, "fd_season"] == "2017" and swe.loc[0, "fd_season_start"] == 2017
    assert swe.loc[0, "fd_league_name"] == "Allsvenskan"
    # All price columns are kept under their own names; facts and junk columns are not prices.
    prices = odds_columns_of(frame)
    assert {"PSH", "PSD", "PSA", "B365CH", "PSCH", "PSCD", "PSCA"} <= set(prices)
    assert not {"Div", "Date", "Country", "League", "Season", "Res", "Unnamed: 9"} & set(prices)
    assert frame["B365CH"].dtype.kind == "f" and pd.isna(e0.loc[1, "B365CH"])
    # Source row ids follow the existing loader convention and are unique.
    assert frame["source_row_id"].is_unique
    assert e0.loc[0, "source_row_id"].startswith("football-data.co.uk:fd.zip::" + MEMBER_MAIN + "E0_1516.csv:")


def test_read_archives_turns_impossible_prices_into_missing_but_keeps_lines_and_counts(tmp_path):
    main_csv = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSCH,AHh,AHCh,BbAH,B365>2.5\n"
        "E0,08/08/15,Arsenal,Chelsea,1,0,0,1.85,0,-0.5,8 ,1.0\n"
        "E0,09/08/15,Everton,Fulham,2,2,2.10,,-0.25,0,,2.5\n"
    )
    archive = make_zip(tmp_path / "fd.zip", {MEMBER_MAIN + "E0_1516.csv": main_csv})

    frame = read_football_data_archives([archive])

    first, second = frame.iloc[0], frame.iloc[1]
    # A price of 0 (or anything not above 1.0) is the provider's "no quote": missing, never a price.
    assert pd.isna(first["PSH"]) and pd.isna(first["B365>2.5"])
    assert first["PSCH"] == pytest.approx(1.85)
    assert second["PSH"] == pytest.approx(2.10) and second["B365>2.5"] == pytest.approx(2.5)
    assert pd.isna(second["PSCH"])  # a blank cell stays missing as well
    # Handicap lines may legitimately be 0 or negative, and the bookmaker count is not a price.
    assert first["AHh"] == 0 and second["AHCh"] == 0 and second["AHh"] == pytest.approx(-0.25)
    assert first["BbAH"] == 8  # a trailing non-breaking space does not turn the count into missing
    assert frame.attrs["unusable_prices_nulled"] == 2


def test_read_archives_skips_a_member_present_in_two_parts(tmp_path):
    csv = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,08/08/15,Man United,Tottenham,1,0\n"
    first = make_zip(tmp_path / "a.zip", {MEMBER_MAIN + "E0_1516.csv": csv})
    second = make_zip(tmp_path / "b.zip", {MEMBER_MAIN + "E0_1516.csv": csv})

    frame = read_football_data_archives([first, second])

    assert len(frame) == 1


def test_assign_league_ids_uses_division_and_league_name():
    frame = fd_frame(
        [
            fd("a", "A", "B", 1, 0, fd_div="E0"),
            fd("b", "A", "B", 1, 0, fd_div="SWZ", fd_league_name="Super League"),
            fd("c", "A", "B", 1, 0, fd_div="SWZ", fd_league_name="Challenge League"),
            fd("d", "A", "B", 1, 0, fd_div="EC"),
        ]
    )

    assigned = assign_league_ids(frame)

    assert list(assigned["league_id"].astype("Int64")) == [39, 207, pd.NA, pd.NA]


def test_classify_odds_column_covers_both_naming_families():
    assert classify_odds_column("B365H") == ("B365", "1x2", "opening")
    assert classify_odds_column("PSCA") == ("PS", "1x2", "closing")
    assert classify_odds_column("VCH") == ("VC", "1x2", "opening")
    assert classify_odds_column("VCCD") == ("VC", "1x2", "closing")
    assert classify_odds_column("BFECH") == ("BFE", "1x2", "closing")
    assert classify_odds_column("Max>2.5") == ("Max", "total_goals_2.5", "opening")
    assert classify_odds_column("PC<2.5") == ("P", "total_goals_2.5", "closing")
    assert classify_odds_column("AvgCAHA") == ("Avg", "asian_handicap", "closing")
    assert classify_odds_column("AHh") == ("", "asian_handicap_line", "opening")
    assert classify_odds_column("AHCh") == ("", "asian_handicap_line", "closing")
    assert classify_odds_column("BbMxH") == ("BbMx", "1x2", "opening")
    assert classify_odds_column("Bb1X2") == ("", "bookmaker_count", "meta")
    assert classify_odds_column("Something") == ("", "unknown", "other")


# --- output --------------------------------------------------------------------------------


def joined_result():
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Everton", "Fulham", 0, 0, home_id=3, away_id=4, day="2024-08-17"),
        ]
    )
    rows = fd_frame(
        [
            fd("a", "Arsenal", "Chelsea", 2, 1, PSCH=1.8, **{"Max>2.5": 1.7, "AHCh": -0.5}),
            fd("b", "Everton", "Fulham", 0, 0, day="2024-08-17", PSCH=None, **{"Max>2.5": None, "AHCh": None}),
            fd("c", "Everton", "Fulham", 3, 3, day="2024-09-20", PSCH=2.0, **{"Max>2.5": 1.5, "AHCh": 0.0}),
            fd("d", "Nobody", "Fulham", 1, 0, PSCH=2.0, **{"Max>2.5": 1.5, "AHCh": 0.0}),
        ],
        odds=["PSCH", "Max>2.5", "AHCh"],
    )
    return join_matches(rows, api, resolver_for(api))


def test_database_keeps_every_price_column_and_nulls(tmp_path):
    result = joined_result()
    path = tmp_path / "out" / "fd_af_matches.sqlite"

    write_join_database(path, result, meta={"note": "unit test"})

    with closing(sqlite3.connect(path)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(fd_af_matches)")]
        assert {"fixture_id", "fd_source_row_id", "PSCH", "Max>2.5", "AHCh"} <= set(columns)
        rows = conn.execute(
            'SELECT fd_source_row_id, fixture_id, PSCH, "Max>2.5", AHCh FROM fd_af_matches ORDER BY fixture_id'
        ).fetchall()
        assert rows == [("a", 1, 1.8, 1.7, -0.5), ("b", 2, None, None, None)]
        pk = [row[1] for row in conn.execute("PRAGMA table_info(fd_af_matches)") if row[5]]
        assert pk == ["fixture_id"]
        reasons = dict(conn.execute("SELECT fd_source_row_id, reason FROM fd_unmatched"))
        assert reasons == {"c": "no_candidate_on_date", "d": "team_alias_missing"}
        catalog = {row[0]: row[1:] for row in conn.execute("SELECT column_name, bookmaker, market, phase FROM odds_columns")}
        assert catalog["PSCH"] == ("PS", "1x2", "closing")
        assert conn.execute("SELECT value FROM join_meta WHERE key='note'").fetchone() == ("unit test",)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fd_af_matches (fixture_id, fd_source_row_id, fd_div, league_id, fd_date, date_diff_days) "
                "VALUES (1, 'x', 'E0', 39, '2024-08-16', 0)"
            )


def test_database_write_replaces_an_existing_file_atomically(tmp_path):
    path = tmp_path / "fd_af_matches.sqlite"
    path.write_bytes(b"not a database")

    write_join_database(path, joined_result(), meta={})

    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM fd_af_matches").fetchone() == (2,)
    assert not list(tmp_path.glob("*.tmp"))


def test_summary_counts_reasons_per_league_and_season():
    table = summarize_by_league_season(joined_result())

    row = table.iloc[0]
    assert row["fd_div"] == "E0" and row["fd_season_start"] == 2024
    assert row["fd_rows"] == 4
    assert row["matched"] == 2
    assert row["no_candidate_on_date"] == 1
    assert row["team_alias_missing"] == 1
    assert row["eligible"] == 4
    assert row["match_rate"] == pytest.approx(0.5)


def test_report_is_polish_and_lists_reasons_counts_and_details():
    report = render_report(joined_result(), generated_at="2026-09-29 12:00 UTC")

    assert report.startswith("# ")
    assert "niepołączone" in report.lower()
    assert "brak kandydata" in report.lower()
    assert "brak aliasu" in report.lower()
    assert "Nobody" in report  # the unresolved team is named
    assert "2024/2025" in report or "2024" in report
    assert "50" in report  # the match rate of the toy input (2 of 4)


def test_report_explains_reasons_and_known_causes():
    api = api_frame([fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2)])
    rows = fd_frame(
        [
            fd("a", "Arsenal", "Chelsea", 2, 1),
            fd("b", "Arsenal", "Chelsea", 0, 3, day="2024-09-30", fd_div="DNK"),
        ]
    )
    dnk_api = api_frame([fx(2, "Arsenal", "Chelsea", 1, 0, home_id=1, away_id=2, league_id=119, day="2024-08-01")])
    result = join_matches(rows, pd.concat([api, dnk_api], ignore_index=True), resolver_for(pd.concat([api, dnk_api])))

    report = render_report(result, generated_at="2026-09-29 12:00 UTC")

    assert "Co oznaczają przyczyny" in report
    assert "Sprawdzone przyczyny" in report
    assert "rundzie spadkowej" in report  # the checked Danish note appears only because DNK rows are unmatched


# --- end to end -------------------------------------------------------------------------------


def make_history_db(path, api: pd.DataFrame) -> None:
    with closing(sqlite3.connect(path)) as conn:
        api.to_sql("matches", conn, index=False)
        conn.commit()


def csv_date(iso_day: str) -> str:
    year, month, day = iso_day.split("-")
    return f"{day}/{month}/{year}"


def test_load_api_matches_is_read_only_and_can_filter_leagues(tmp_path):
    api = api_frame(
        [
            fx(1, "Arsenal", "Chelsea", 2, 1, home_id=1, away_id=2),
            fx(2, "Barca", "Betis", 0, 0, home_id=3, away_id=4, league_id=140, league_code="LALIGA"),
        ]
    )
    path = tmp_path / "history.sqlite"
    make_history_db(path, api)
    before = path.read_bytes()

    from model_buk.ingestion.fd_af_join import load_api_matches

    only_spain = load_api_matches(path, [140])
    everything = load_api_matches(path)

    assert list(only_spain["fixture_id"]) == [2]
    assert len(everything) == 2
    assert path.read_bytes() == before


def test_run_join_end_to_end_keeps_human_alias_rows_and_refreshes_auto_rows(tmp_path):
    from model_buk.ingestion.fd_af_join import run_join

    api_names = {1: "Manchester City", 2: "Manchester United", 3: "Arsenal", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    fd_names = {1: "Man City", 2: "Man United", 3: "Gunners", 4: "Chelsea", 5: "Liverpool", 6: "Everton"}
    api, fd_rows = double_round_robin(api_names, fd_names)
    history = tmp_path / "history.sqlite"
    make_history_db(history, api)
    lines = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,PSH,PSCH"]
    for row in fd_rows.itertuples(index=False):
        lines.append(
            f"E0,{csv_date(row.fd_date.strftime('%Y-%m-%d'))},{row.fd_home_team},{row.fd_away_team},"
            f"{row.fd_home_goals},{row.fd_away_goals},0,0,2.1,"
        )
    archive = make_zip(tmp_path / "fd.zip", {MEMBER_MAIN + "E0_2425.csv": "\n".join(lines) + "\n"})
    aliases = tmp_path / "aliases.csv"
    save_aliases(
        aliases,
        [
            AliasRow(39, "E0", "Gunners", 3, "Arsenal", "manual", None, "kept by hand"),
            AliasRow(39, "E0", "Ghost FC", 6, "Everton", "auto", 9, "stale auto row"),
        ],
    )
    out_dir = tmp_path / "joined"

    summary = run_join(archives=[archive], history_db=history, aliases_path=aliases, out_dir=out_dir)

    assert len(summary.result.matched) == 30 and summary.result.unmatched.empty
    loaded = {row.fd_team: row for row in load_aliases(aliases)}
    assert set(loaded) == {"Gunners", "Man City", "Man United"}  # the stale auto row is gone
    assert loaded["Gunners"].source == "manual" and loaded["Gunners"].note == "kept by hand"
    with closing(sqlite3.connect(out_dir / "fd_af_matches.sqlite")) as conn:
        assert conn.execute("SELECT count(*), count(DISTINCT fixture_id) FROM fd_af_matches").fetchone() == (30, 30)
        assert conn.execute("SELECT count(*) FROM fd_af_matches WHERE PSH = 2.1 AND PSCH IS NULL").fetchone() == (30,)
        assert conn.execute(
            "SELECT value FROM join_meta WHERE key = 'price_cells_not_above_1_set_to_null'"
        ).fetchone() == ("0",)
    report = (out_dir / "unmatched_report.md").read_text(encoding="utf-8")
    assert "100.00%" in report

    # A second run without seeding must leave the alias file exactly as it is.
    snapshot = aliases.read_bytes()
    run_join(archives=[archive], history_db=history, aliases_path=aliases, out_dir=out_dir, seed=False)
    assert aliases.read_bytes() == snapshot
