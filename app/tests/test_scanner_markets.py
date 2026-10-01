"""Market dictionary and point-in-time quote loading of the scanner (real bet types and selection strings)."""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from model_buk.ingestion.api_football_odds_archive import OddsArchive
from model_buk.scanner.markets import (
    CANONICAL_MARKETS,
    QUOTE_COLUMNS,
    canonicalize,
    dedupe_legs,
    load_closing_quotes,
    load_current_quotes,
    map_bet,
    mapped_share,
)

NAN = float("nan")


def same(actual, expected):
    """Compare (market, period, line, selection) tuples with NaN == NaN."""
    assert actual is not None
    assert actual[0] == expected[0] and actual[1] == expected[1] and actual[3] == expected[3]
    if math.isnan(expected[2]):
        assert math.isnan(actual[2])
    else:
        assert actual[2] == pytest.approx(expected[2])


# (bet_id, raw selection) -> (market, period, line, selection), strings as they appear in odds.sqlite3
MAPPING_TABLE = [
    # match result
    (1, "Home", ("1x2", "ft", NAN, "home")),
    (1, "Draw", ("1x2", "ft", NAN, "draw")),
    (13, "Away", ("1x2", "1h", NAN, "away")),
    (3, "Draw", ("1x2", "2h", NAN, "draw")),
    (2, "Home", ("dnb", "ft", NAN, "home")),
    (109, "Away", ("dnb", "1h", NAN, "away")),
    (182, "Home", ("dnb", "2h", NAN, "home")),
    # double chance, every spelling the feed uses
    (12, "Home/Draw", ("dc", "ft", NAN, "1x")),
    (12, "Draw/Home", ("dc", "ft", NAN, "1x")),
    (12, "Home/Away", ("dc", "ft", NAN, "12")),
    (12, "Away/Home", ("dc", "ft", NAN, "12")),
    (12, "Draw/Away", ("dc", "ft", NAN, "x2")),
    (12, "Away/Draw", ("dc", "ft", NAN, "x2")),
    (20, "Draw/Home", ("dc", "1h", NAN, "1x")),
    (33, "Home/Away", ("dc", "2h", NAN, "12")),
    # Asian handicap: both sides carry the handicap of the HOME team
    (4, "Home -0.25", ("ah", "ft", -0.25, "home")),
    (4, "Away -0.25", ("ah", "ft", -0.25, "away")),
    (4, "Away +0", ("ah", "ft", 0.0, "away")),
    (4, "Home +1.75", ("ah", "ft", 1.75, "home")),
    (4, "Away +2.5", ("ah", "ft", 2.5, "away")),
    (4, "Home -9.5", ("ah", "ft", -9.5, "home")),
    (19, "Away +0.5", ("ah", "1h", 0.5, "away")),
    (104, "Home -2.75", ("ah", "2h", -2.75, "home")),
    # goals over/under: "Over 2.0", "Over 2" and "Over 2.00" are one line
    (5, "Over 2.5", ("ou", "ft", 2.5, "over")),
    (5, "Over 2.0", ("ou", "ft", 2.0, "over")),
    (5, "Under 3.25", ("ou", "ft", 3.25, "under")),
    (6, "Over 0.75", ("ou", "1h", 0.75, "over")),
    (26, "Under 1.5", ("ou", "2h", 1.5, "under")),
    (50, "Over 2.25", ("ou", "ft", 2.25, "over")),
    (72, "Under 1", ("ou", "1h", 1.0, "under")),
    # team totals
    (16, "Over 1.5", ("team_ou_home", "ft", 1.5, "over")),
    (16, "Under 1", ("team_ou_home", "ft", 1.0, "under")),
    (17, "Under 0.5", ("team_ou_away", "ft", 0.5, "under")),
    (105, "Over 1.5", ("team_ou_home", "1h", 1.5, "over")),
    (106, "Over 0.5", ("team_ou_away", "1h", 0.5, "over")),
    (107, "Under 2.5", ("team_ou_home", "2h", 2.5, "under")),
    (108, "Under 0.5", ("team_ou_away", "2h", 0.5, "under")),
    # both teams to score, exact score
    (8, "Yes", ("btts", "ft", NAN, "yes")),
    (34, "No", ("btts", "1h", NAN, "no")),
    (35, "Yes", ("btts", "2h", NAN, "yes")),
    (10, "1:0", ("cs", "ft", NAN, "1:0")),
    (10, "10:2", ("cs", "ft", NAN, "10:2")),
    (31, "0:0", ("cs", "1h", NAN, "0:0")),
    (62, "2:1", ("cs", "2h", NAN, "2:1")),
    # corners
    (45, "Over 10.25", ("corners_ou", "ft", 10.25, "over")),
    (57, "Under 4.5", ("corners_home_ou", "ft", 4.5, "under")),
    (58, "Over 2.5", ("corners_away_ou", "ft", 2.5, "over")),
    (77, "Over 4.5", ("corners_ou", "1h", 4.5, "over")),
    (127, "Under 5.75", ("corners_ou", "2h", 5.75, "under")),
    (132, "Over 2", ("corners_home_ou", "1h", 2.0, "over")),
    (135, "Under 3.5", ("corners_away_ou", "2h", 3.5, "under")),
    (56, "Home -4.5", ("corners_ah", "ft", -4.5, "home")),
    (125, "Away +0.25", ("corners_ah", "1h", 0.25, "away")),
    (55, "Draw", ("corners_1x2", "ft", NAN, "draw")),
    (130, "Home", ("corners_1x2", "1h", NAN, "home")),
    # cards
    (80, "Over 3.5", ("cards_ou", "ft", 3.5, "over")),
    (81, "Home -1", ("cards_ah", "ft", -1.0, "home")),
    (82, "Under 1.5", ("cards_home_ou", "ft", 1.5, "under")),
    (83, "Over 0.5", ("cards_away_ou", "ft", 0.5, "over")),
    # the same event in another formulation
    (27, "Yes", ("team_ou_away", "ft", 0.5, "under")),  # home keeps a clean sheet
    (28, "No", ("team_ou_home", "ft", 0.5, "over")),
    (43, "Yes", ("team_ou_home", "ft", 0.5, "over")),
    (44, "No", ("team_ou_away", "ft", 0.5, "under")),
    (114, "Yes", ("team_ou_home", "1h", 0.5, "over")),
    (117, "No", ("team_ou_away", "2h", 0.5, "under")),
]


@pytest.mark.parametrize(("bet_id", "selection", "expected"), MAPPING_TABLE)
def test_real_bet_and_selection_strings_map_to_canonical_legs(bet_id, selection, expected):
    same(map_bet(bet_id, selection), expected)
    assert expected[0] in CANONICAL_MARKETS


@pytest.mark.parametrize(
    ("bet_id", "selection"),
    [
        (7, "Home/Over"),  # HT/FT with a malformed half
        (21, "Odd 1"),
        (25, "Home/Over 2.5"),  # result and total
        (9, "Home -1.5"),  # a European handicap is a whole number of goals
        (9, "Home"),
        (11, "3rd Half"),
        (14, "Nobody"),
        (36, "Draw"),  # "Win To Nil" names a team
        (38, "more 6"),
        (92, "Erling Haaland"),
        (153, "Over 2.5"),  # yellow cards only, not "cards"
        (150, "Over 0.5"),
        (10, "Other"),  # not an h:a score
        (5, "Over 2.6"),  # not a quarter line
        (4, "Home"),  # handicap without a number
        (12, "Home/Home"),
        (12, "Home"),
        (999999, "Home"),
    ],
)
def test_unmapped_bet_types_and_malformed_selections_are_none(bet_id, selection):
    assert map_bet(bet_id, selection) is None


def test_asian_handicap_home_and_away_of_one_line_share_the_home_handicap():
    raw = pd.DataFrame(
        {
            "bet_id": [4, 4, 4, 4, 4],
            "selection": ["Home -0.5", "Away -0.5", "Home +0.5", "Away +0.5", "Home -0"],
            "odd": [1.52, 2.47, 3.1, 1.36, 1.9],
        }
    )
    out = canonicalize(raw)
    assert out["line"].tolist() == [-0.5, -0.5, 0.5, 0.5, 0.0]
    assert out["selection"].tolist() == ["home", "away", "home", "away", "home"]
    # a "-0" line must be a plain zero, or the same line would be two different join keys
    assert not np.signbit(out["line"].iloc[-1])
    # each line has exactly one home and one away price: they are the two sides of one bet
    counts = out.groupby(["market", "line"])["selection"].agg(lambda s: tuple(sorted(s)))
    assert counts.loc[("ah", -0.5)] == ("away", "home")
    assert counts.loc[("ah", 0.5)] == ("away", "home")


def test_canonicalize_keeps_rows_order_and_marks_other_rows_raw():
    raw = pd.DataFrame(
        {
            "fixture_id": [1, 1, 1, 1],
            "bookmaker_id": [4, 4, 4, 4],
            "bookmaker": ["Pinnacle"] * 4,
            "bet_id": [1, 25, 92, 5],
            "bet": ["Match Winner", "Result/Total Goals", "Anytime Goal Scorer", "Goals Over/Under"],
            "selection": ["Draw", "Home/Over 2.5", "Some Player", "Over 2.0"],
            "odd": [3.4, 2.1, 4.5, 1.9],
        }
    )
    out = canonicalize(raw)
    assert out["odd"].tolist() == raw["odd"].tolist()  # order and other columns untouched
    assert out["bet"].tolist() == raw["bet"].tolist()
    assert out["raw_selection"].tolist() == raw["selection"].tolist()
    other = out[out["market"] == "other"]
    assert other["bet_id"].tolist() == [25, 92]
    assert other["selection"].tolist() == ["Home/Over 2.5", "Some Player"]  # the raw text is kept
    assert (other["period"] == "na").all() and other["line"].isna().all()
    assert not out["three_way"].any()
    assert out.loc[3, ["market", "period", "line", "selection"]].tolist() == ["ou", "ft", 2.0, "over"]


def test_three_way_totals_map_to_the_equivalent_half_lines():
    rows = []
    # Bet365 corners three-way: Over N / Exactly N / Under N at integer N
    for selection, odd in [("Over 10", 2.25), ("Exactly 10", 8.0), ("Under 10", 2.11), ("Over 10.5", 2.6), ("Under 10.5", 1.5)]:
        rows.append((1, 8, "Bet365", 85, selection, odd))
    # bet 77 at Bet365 mixes Exactly rows with lines; Pinnacle quotes plain two-way integer lines in the same bet
    for selection, odd in [("Exactly 4", 5.0), ("Over 4", 1.91), ("Under 4", 2.75)]:
        rows.append((1, 8, "Bet365", 77, selection, odd))
    for selection, odd in [("Over 4", 1.51), ("Under 4", 2.37)]:
        rows.append((1, 4, "Pinnacle", 77, selection, odd))
    raw = pd.DataFrame(rows, columns=["fixture_id", "bookmaker_id", "bookmaker", "bet_id", "selection", "odd"])
    out = canonicalize(raw)

    def leg(row):
        return tuple(out.loc[row, ["market", "period", "line", "selection", "three_way"]])

    assert leg(0) == ("corners_ou", "ft", 10.5, "over", True)  # total >= 11
    assert leg(1)[0] == "other" and not out.loc[1, "three_way"]  # "Exactly 10" is not a canonical leg
    assert leg(2) == ("corners_ou", "ft", 9.5, "under", True)  # total <= 9
    assert leg(3) == ("corners_ou", "ft", 10.5, "over", False)  # a half line is two-way in any bet
    assert leg(4) == ("corners_ou", "ft", 10.5, "under", False)
    assert leg(6) == ("corners_ou", "1h", 4.5, "over", True)
    assert leg(7) == ("corners_ou", "1h", 3.5, "under", True)
    assert leg(8) == ("corners_ou", "1h", 4.0, "over", False)  # Pinnacle: two-way asian integer line, push possible
    assert leg(9) == ("corners_ou", "1h", 4.0, "under", False)


def test_dedupe_keeps_the_highest_price_of_the_same_event():
    raw = pd.DataFrame(
        {
            "fixture_id": [1, 1, 1],
            "bookmaker_id": [8, 8, 8],
            "bookmaker": ["Bet365"] * 3,
            "bet_id": [5, 50, 5],
            "selection": ["Over 2.5", "Over 2.5", "Under 2.5"],
            "odd": [2.20, 2.25, 1.65],
        }
    )
    out = dedupe_legs(canonicalize(raw))
    assert len(out) == 2
    assert out.loc[out["selection"] == "over", "odd"].iloc[0] == 2.25


# --------------------------------------------------------------------------- #
# Loading from a real archive file
# --------------------------------------------------------------------------- #

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
KICKOFF = T0 + timedelta(hours=5)


def bookmaker(bookmaker_id, name, *bets):
    return {"id": bookmaker_id, "name": name, "bets": [{"id": b, "name": n, "values": v} for b, n, v in bets]}


def obj(fixture_id, kickoff, *bookmakers, league_id=39):
    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": league_id, "season": 2026},
        "update": T0.isoformat(),
        "bookmakers": list(bookmakers),
    }


def raw_values(pairs):
    return [{"value": value, "odd": str(odd)} for value, odd in pairs]


def one_x_two(home, draw, away):
    return (1, "Match Winner", raw_values([("Home", home), ("Draw", draw), ("Away", away)]))


@pytest.fixture()
def archive(tmp_path):
    return OddsArchive(tmp_path)


def test_current_quotes_are_point_in_time(archive):
    t1, t2, t3 = T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    pinnacle = lambda home: bookmaker(4, "Pinnacle", one_x_two(home, 3.6, 3.9))  # noqa: E731
    archive.record("s1", t1, [obj(10, KICKOFF, pinnacle(2.10))])
    archive.record("s2", t2, [obj(10, KICKOFF, pinnacle(2.10))])  # unchanged price, seen again
    archive.record("s3", t3, [obj(10, KICKOFF, pinnacle(1.95))])  # the home price moves

    between = load_current_quotes(archive.db_path, t2 + timedelta(minutes=30))
    home = between[between["selection"] == "home"].iloc[0]
    assert home["odd"] == 2.10  # the later move is not visible yet
    assert home["fetched_at"] == pd.Timestamp(t2)  # last sighting of the board, not the last change
    assert home["changed_at"] == pd.Timestamp(t1)
    assert home["age_minutes"] == pytest.approx(30.0)
    assert (between["market"] == "1x2").all() and set(between["selection"]) == {"home", "draw", "away"}
    assert set(QUOTE_COLUMNS) <= set(between.columns)

    after = load_current_quotes(archive.db_path, t3 + timedelta(minutes=10))
    home = after[after["selection"] == "home"].iloc[0]
    assert home["odd"] == 1.95 and home["changed_at"] == pd.Timestamp(t3)
    assert after.loc[after["selection"] == "draw", "changed_at"].iloc[0] == pd.Timestamp(t1)

    assert load_current_quotes(archive.db_path, t1 - timedelta(minutes=1)).empty  # nothing was known yet


def test_current_quotes_only_for_fixtures_that_have_not_started(archive):
    started = T0 + timedelta(minutes=30)
    later = T0 + timedelta(days=3)
    pin = bookmaker(4, "Pinnacle", one_x_two(2.0, 3.5, 4.0))
    archive.record("s1", T0, [obj(1, started, pin), obj(2, KICKOFF, pin), obj(3, later, pin)])
    as_of = T0 + timedelta(hours=1)
    quotes = load_current_quotes(archive.db_path, as_of)
    assert set(quotes["fixture_id"]) == {2, 3}  # fixture 1 kicked off half an hour before as_of
    assert set(load_current_quotes(archive.db_path, as_of, horizon_days=1)["fixture_id"]) == {2}


def test_invalid_prices_are_dropped_and_other_rows_are_optional(archive):
    board = bookmaker(
        8,
        "Bet365",
        (1, "Match Winner", raw_values([("Home", 1.0), ("Draw", 3.5), ("Away", 4.0)])),
        (92, "Anytime Goal Scorer", raw_values([("Some Player", 3.0)])),
    )
    archive.record("s1", T0, [obj(1, KICKOFF, board)])
    with_other = load_current_quotes(archive.db_path, T0 + timedelta(minutes=5))
    assert len(with_other) == 3  # home @ 1.0 is a suspended selection, not a price
    assert "home" not in set(with_other.loc[with_other["market"] == "1x2", "selection"])
    only_legs = load_current_quotes(archive.db_path, T0 + timedelta(minutes=5), include_other=False)
    assert set(only_legs["market"]) == {"1x2"} and len(only_legs) == 2
    share = mapped_share(with_other).set_index("bookmaker")
    assert share.loc["Bet365", "n_quotes"] == 3 and share.loc["Bet365", "n_mapped"] == 2


def test_closing_quotes_are_the_last_quote_strictly_before_kickoff(archive):
    kickoff = T0 + timedelta(hours=6)
    board = lambda home: bookmaker(4, "Pinnacle", one_x_two(home, 3.5, 4.0))  # noqa: E731
    archive.record("s1", kickoff - timedelta(hours=5), [obj(1, kickoff, board(2.20))])
    archive.record("s2", kickoff - timedelta(hours=2), [obj(1, kickoff, board(2.05))])
    archive.record("s3", kickoff - timedelta(hours=1), [obj(1, kickoff, board(2.05))])  # seen again, unchanged
    archive.record("s4", kickoff, [obj(1, kickoff, board(1.80))])  # a quote at kickoff is not a closing price
    archive.record("s5", kickoff + timedelta(minutes=10), [obj(1, kickoff, board(1.50))])  # nor is one after it
    archive.record("s0", T0, [obj(2, kickoff, board(3.0))])  # a second fixture with a single early quote

    closing_quotes = load_closing_quotes(archive.db_path, [1, 2, 3])
    home = closing_quotes[(closing_quotes["selection"] == "home") & (closing_quotes["fixture_id"] == 1)].iloc[0]
    assert home["odd"] == 2.05
    assert home["changed_at"] == pd.Timestamp(kickoff - timedelta(hours=2))
    assert home["fetched_at"] == pd.Timestamp(kickoff - timedelta(hours=1))
    assert home["age_minutes"] == pytest.approx(60.0)  # how long before the start the close was last seen
    second = closing_quotes[(closing_quotes["fixture_id"] == 2) & (closing_quotes["selection"] == "home")].iloc[0]
    assert second["odd"] == 3.0
    assert set(closing_quotes["fixture_id"]) == {1, 2}  # fixture 3 does not exist
    assert load_closing_quotes(archive.db_path, [99]).empty


def test_quote_frames_survive_an_empty_archive(archive):
    empty = load_current_quotes(archive.db_path, T0)
    assert list(empty.columns)[: len(QUOTE_COLUMNS)] == list(QUOTE_COLUMNS)
    assert load_closing_quotes(archive.db_path, []).empty


def test_reading_does_not_write_to_the_archive(archive):
    archive.record("s1", T0, [obj(1, KICKOFF, bookmaker(4, "Pinnacle", one_x_two(2.0, 3.5, 4.0)))])
    with closing(sqlite3.connect(archive.db_path)) as connection:
        before = connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    load_current_quotes(archive.db_path, T0 + timedelta(hours=1))
    load_closing_quotes(archive.db_path, [1])
    with closing(sqlite3.connect(archive.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before


def test_provider_age_is_reported_next_to_the_sighting_age(archive):
    # the provider's own update time lags our fetch by hours: the board is "seen 30 minutes ago" but old at source
    def with_update(fetched_at, update):
        board = obj(10, KICKOFF, bookmaker(4, "Pinnacle", one_x_two(2.10, 3.6, 3.9)))
        board["update"] = update.isoformat()
        archive.record(f"s{fetched_at.hour}", fetched_at, [board])

    with_update(T0 + timedelta(hours=1), T0 - timedelta(hours=2))
    with_update(T0 + timedelta(hours=2), T0 - timedelta(hours=1, minutes=30))
    as_of = T0 + timedelta(hours=2, minutes=30)
    quotes = load_current_quotes(archive.db_path, as_of)
    home = quotes[quotes["selection"] == "home"].iloc[0]
    assert home["age_minutes"] == pytest.approx(30.0)  # last sighting 30 minutes ago
    assert home["api_update"] == pd.Timestamp(T0 - timedelta(hours=1, minutes=30))  # update of the LAST sighting
    assert home["provider_age_minutes"] == pytest.approx(4 * 60.0)  # but the provider's snapshot is four hours old
    earlier = load_current_quotes(archive.db_path, T0 + timedelta(hours=1, minutes=30))
    assert earlier["provider_age_minutes"].iloc[0] == pytest.approx(3.5 * 60.0)  # the later scan is not visible yet
    closing_quotes = load_closing_quotes(archive.db_path, [10])
    assert closing_quotes["provider_age_minutes"].iloc[0] == pytest.approx((KICKOFF - (T0 - timedelta(hours=1, minutes=30))).total_seconds() / 60)


def test_unknown_provider_update_stays_missing(archive):
    board = obj(10, KICKOFF, bookmaker(4, "Pinnacle", one_x_two(2.10, 3.6, 3.9)))
    board["update"] = None
    archive.record("s1", T0, [board])
    quotes = load_current_quotes(archive.db_path, T0 + timedelta(minutes=5))
    assert quotes["api_update"].isna().all() and quotes["provider_age_minutes"].isna().all()
    assert quotes["age_minutes"].to_numpy() == pytest.approx(5.0)  # the sighting age does not depend on it


def test_an_archive_in_a_folder_with_uri_special_characters_is_read(tmp_path):
    folder = tmp_path / "odds #1 100%"
    folder.mkdir()
    special = OddsArchive(folder)
    special.record("s1", T0, [obj(1, KICKOFF, bookmaker(4, "Pinnacle", one_x_two(2.0, 3.5, 4.0)))])
    quotes = load_current_quotes(special.db_path, T0 + timedelta(minutes=5))
    assert set(quotes["selection"]) == {"home", "draw", "away"}  # the right file, not a cut-off path


def test_a_load_reads_the_archive_inside_one_transaction(archive):
    from model_buk.scanner.markets import _connect

    archive.record("s1", T0, [obj(1, KICKOFF, bookmaker(4, "Pinnacle", one_x_two(2.0, 3.5, 4.0)))])
    with closing(_connect(archive.db_path)) as connection:
        assert connection.in_transaction  # the quote and the observation queries see one state of the archive
        with pytest.raises(sqlite3.OperationalError):  # and it is read-only
            connection.execute("DELETE FROM quotes")


# --------------------------------------------------------------------------- #
# Handicap ladders that arrive with the opposite sign
# --------------------------------------------------------------------------- #


def ladder(fixture, bookmaker_id, market, lines, home_odds, *, bet_id=4):
    rows = []
    for line, odd in zip(lines, home_odds, strict=True):
        rows.append({"fixture_id": fixture, "bookmaker_id": bookmaker_id, "bookmaker": f"B{bookmaker_id}", "bet_id": bet_id, "market": market,
                     "period": "ft", "line": line, "selection": "home", "odd": odd})
        rows.append({"fixture_id": fixture, "bookmaker_id": bookmaker_id, "bookmaker": f"B{bookmaker_id}", "bet_id": bet_id, "market": market,
                     "period": "ft", "line": line, "selection": "away", "odd": 1.0 / (1.05 - 1.0 / odd)})
    return rows


LINES = [-1.0, -0.5, 0.0, 0.5, 1.0]
FALLING = [3.2, 2.2, 1.7, 1.4, 1.25]  # a correct ladder: the home price falls as its handicap rises
RISING = FALLING[::-1]


def test_a_reversed_handicap_ladder_is_flipped_for_the_whole_fixture_and_market():
    from model_buk.scanner.markets import orient_handicaps

    rows = (
        ladder(1, 4, "ah", LINES, FALLING)  # fixture 1: correct
        + ladder(2, 4, "ah", LINES, RISING)  # fixture 2: reversed, a full ladder
        + ladder(2, 8, "ah", [-0.5, 0.5], [2.0, 1.4][::-1])  # a short ladder of another book: no evidence of its own
        + ladder(2, 4, "ou", [1.5, 2.5], [1.5, 2.3], bet_id=5)  # other markets are never touched
    )
    frame_in = pd.DataFrame(rows)
    frame_in.loc[frame_in["market"] == "ou", "selection"] = frame_in["selection"].map({"home": "over", "away": "under"})
    out = orient_handicaps(frame_in)
    ah = out[out["market"] == "ah"]
    assert not ah[ah["fixture_id"] == 1]["handicap_flipped"].any()
    assert (ah[ah["fixture_id"] == 1]["line"].to_numpy() == frame_in[(frame_in["market"] == "ah") & (frame_in["fixture_id"] == 1)]["line"].to_numpy()).all()
    flipped = ah[ah["fixture_id"] == 2]
    assert flipped["handicap_flipped"].all()
    assert sorted(flipped[flipped["bookmaker_id"] == 4]["line"].unique()) == sorted(-l + 0.0 for l in LINES)
    assert sorted(flipped[flipped["bookmaker_id"] == 8]["line"].unique()) == [-0.5, 0.5]  # the short ladder follows the fixture
    assert not out[out["market"] == "ou"]["handicap_flipped"].any()
    assert not np.signbit(out.loc[out["line"] == 0.0, "line"]).any()  # no negative zero
    # the corrected ladder is monotone the right way: home price falls as the home handicap rises
    home = flipped[(flipped["bookmaker_id"] == 4) & (flipped["selection"] == "home")].sort_values("line")
    assert home["odd"].is_monotonic_decreasing


def test_short_or_contradictory_ladders_are_left_as_labelled():
    from model_buk.scanner.markets import orient_handicaps

    short = pd.DataFrame(ladder(1, 4, "ah", [-0.5, 0.0, 0.5], [1.4, 1.7, 2.2]))  # reversed but only three lines
    assert not orient_handicaps(short)["handicap_flipped"].any()
    two_short = pd.DataFrame(ladder(1, 4, "ah", [-0.5, 0.0, 0.5], [1.4, 1.7, 2.2]) + ladder(1, 8, "ah", [-0.5, 0.0, 0.5], [1.5, 1.8, 2.3]))
    assert not orient_handicaps(two_short)["handicap_flipped"].any()  # two ladders of three lines agree, still too short
    four = pd.DataFrame(ladder(1, 4, "ah", LINES[:4], RISING[:4]))  # one reversed ladder of four lines is not proof
    assert not orient_handicaps(four)["handicap_flipped"].any()
    two_of_four = pd.DataFrame(ladder(1, 4, "ah", LINES[:4], RISING[:4]) + ladder(1, 8, "ah", LINES[:4], RISING[:4]))
    assert orient_handicaps(two_of_four)["handicap_flipped"].all()  # two books agree
    conflict = pd.DataFrame(ladder(1, 4, "ah", LINES, FALLING) + ladder(1, 8, "ah", LINES, RISING))
    result = orient_handicaps(conflict)
    assert not result["handicap_flipped"].any()
    assert result["line"].tolist() == conflict["line"].tolist()
    noisy = pd.DataFrame(ladder(1, 4, "ah", LINES, [2.0, 2.05, 1.95, 2.0, 2.02]))  # flat: no correlation
    assert not orient_handicaps(noisy)["handicap_flipped"].any()
    corners = pd.DataFrame(ladder(1, 4, "corners_ah", LINES, RISING, bet_id=56))
    assert orient_handicaps(corners)["handicap_flipped"].all()  # corner and card handicaps are checked the same way
    assert orient_handicaps(pd.DataFrame(columns=["fixture_id", "market", "selection", "line", "odd"])).empty


def test_loaders_correct_a_reversed_ladder_end_to_end(archive):
    values = lambda pairs: [{"value": v, "odd": str(o)} for v, o in pairs]  # noqa: E731
    pairs = []
    for line, home in zip(LINES, RISING, strict=True):
        label = f"{line:+g}" if line else "+0"
        pairs += [(f"Home {label}", home), (f"Away {label}", round(1.0 / (1.05 - 1.0 / home), 2))]
    board = bookmaker(4, "Pinnacle", (4, "Asian Handicap", values(pairs)))
    archive.record("s1", T0, [obj(7, KICKOFF, board)])
    quotes = load_current_quotes(archive.db_path, T0 + timedelta(minutes=5))
    assert quotes["handicap_flipped"].all()
    assert sorted(quotes["line"].unique()) == sorted(-l + 0.0 for l in LINES)


# --------------------------------------------------------------------------- #
# Broken bookmaker ladders
# --------------------------------------------------------------------------- #


def priced_ladder(fixture, bookmaker_id, name, lines, q_home, *, margin=1.05, market="ah"):
    """A ladder whose home probability at every line is ``q_home``; both prices carry a proportional margin."""
    rows = []
    for line, q in zip(lines, q_home, strict=True):
        for selection, probability in (("home", q), ("away", 1.0 - q)):
            rows.append({"fixture_id": fixture, "bookmaker_id": bookmaker_id, "bookmaker": name, "bet_id": 4, "market": market, "period": "ft",
                         "line": line, "selection": selection, "odd": round(1.0 / (probability * margin), 3), "three_way": False})
    return rows


def test_a_flat_ladder_is_flagged_against_pinnacle_and_a_healthy_one_is_not():
    from model_buk.scanner.markets import flag_incoherent_ladders

    lines = [-1.0, -0.75, -0.5, -0.25, 0.0]
    truth = [0.36, 0.43, 0.50, 0.58, 0.70]
    rows = (
        priced_ladder(1, 4, "Pinnacle", lines, truth)
        + priced_ladder(1, 8, "Bet365", lines, [0.47, 0.475, 0.50, 0.53, 0.53])  # every line carries a balanced pair
        + priced_ladder(1, 11, "1xBet", lines, [q + 0.02 for q in truth])  # a level shift is still a ladder
        + priced_ladder(1, 32, "Betano", lines[:2], truth[:2])  # two lines only: cannot be assessed
        + priced_ladder(2, 8, "Bet365", lines, [0.47, 0.475, 0.50, 0.53, 0.53])  # no Pinnacle ladder for this fixture
        + priced_ladder(3, 4, "Pinnacle", lines, [0.50, 0.50, 0.51, 0.51, 0.52])  # a Pinnacle ladder that spans nothing
        + priced_ladder(3, 8, "Bet365", lines, [0.50, 0.50, 0.51, 0.51, 0.52])
    )
    frame_in = pd.DataFrame(rows)
    out = flag_incoherent_ladders(frame_in)

    def slope(fixture, name):
        return out[(out["fixture_id"] == fixture) & (out["bookmaker"] == name)]

    assert slope(1, "Bet365")["ladder_suspect"].all() and slope(1, "Bet365")["ladder_slope"].iloc[0] < 0.3
    assert not slope(1, "1xBet")["ladder_suspect"].any()
    assert slope(1, "1xBet")["ladder_slope"].iloc[0] == pytest.approx(1.0, abs=0.1)
    for fixture, name in [(1, "Betano"), (1, "Pinnacle"), (2, "Bet365"), (3, "Bet365")]:
        assert not slope(fixture, name)["ladder_suspect"].any()
        assert slope(fixture, name)["ladder_slope"].isna().all()  # not assessable: unknown, never zero
    assert len(out) == len(frame_in)


def test_loaders_drop_suspect_ladders_unless_asked_to_keep_them(archive):
    lines = [-1.0, -0.75, -0.5, -0.25, 0.0]
    truth = [0.36, 0.43, 0.50, 0.58, 0.70]

    def board(bookmaker_id, name, q_home):
        pairs = []
        for line, q in zip(lines, q_home, strict=True):
            label = f"{line:+g}" if line else "+0"
            pairs += [(f"Home {label}", round(1.0 / (q * 1.05), 3)), (f"Away {label}", round(1.0 / ((1 - q) * 1.05), 3))]
        return bookmaker(bookmaker_id, name, (4, "Asian Handicap", [{"value": v, "odd": str(o)} for v, o in pairs]))

    archive.record("s1", T0, [obj(5, KICKOFF, board(4, "Pinnacle", truth), board(8, "Bet365", [0.47, 0.475, 0.50, 0.53, 0.53]))])
    as_of = T0 + timedelta(minutes=5)
    kept = load_current_quotes(archive.db_path, as_of)
    assert set(kept["bookmaker"]) == {"Pinnacle"} and not kept["ladder_suspect"].any()
    everything = load_current_quotes(archive.db_path, as_of, include_suspect_ladders=True)
    assert set(everything["bookmaker"]) == {"Pinnacle", "Bet365"}
    assert everything.loc[everything["bookmaker"] == "Bet365", "ladder_suspect"].all()
    closing_kept = load_closing_quotes(archive.db_path, [5])
    assert set(closing_kept["bookmaker"]) == {"Pinnacle"}
    assert set(load_closing_quotes(archive.db_path, [5], include_suspect_ladders=True)["bookmaker"]) == {"Pinnacle", "Bet365"}
