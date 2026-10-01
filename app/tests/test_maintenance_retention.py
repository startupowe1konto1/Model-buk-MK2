"""Retention of the odds archive and the scanner register (model_buk.maintenance), on synthetic databases."""

from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from model_buk.ingestion.api_football_odds_archive import OddsArchive
from model_buk.maintenance import exports as ex
from model_buk.maintenance import odds_retention as oret
from model_buk.maintenance import register_retention as rret
from model_buk.scanner import bet_types, markets
from model_buk.scanner.register import ScannerRegister

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Synthetic odds archive
# --------------------------------------------------------------------------- #


def odds_obj(fixture_id: int, kickoff: datetime, home: float, over: float, *, update: str = "2026-10-01T00:00:00+00:00") -> dict:
    def book(book_id: int, name: str, shift: float) -> dict:
        return {
            "id": book_id,
            "name": name,
            "bets": [
                {
                    "id": 1,
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": f"{home + shift:.2f}"},
                        {"value": "Draw", "odd": "3.40"},
                        {"value": "Away", "odd": "3.60"},
                    ],
                },
                {
                    "id": 5,
                    "name": "Goals Over/Under",
                    "values": [{"value": "Over 2.5", "odd": f"{over:.2f}"}, {"value": "Under 2.5", "odd": "1.90"}],
                },
            ],
        }

    return {
        "fixture": {"id": fixture_id, "date": kickoff.isoformat()},
        "league": {"id": 39, "season": 2026},
        "update": update,
        "bookmakers": [book(4, "Pinnacle", 0.0), book(8, "Bet365", 0.05), book(11, "1xBet", 0.1)],
    }


KICKOFFS = {
    101: NOW - timedelta(days=12),  # old: archived
    102: NOW - timedelta(days=9, hours=1),  # old: archived
    201: NOW - timedelta(days=3),  # inside the 7-day window: kept
    202: NOW - timedelta(days=6, hours=23),  # just inside: kept
    301: NOW + timedelta(days=1),  # not started: kept
}


def build_odds_archive(root) -> OddsArchive:
    archive = OddsArchive(root)
    for fixture_id, kickoff in KICKOFFS.items():
        first = kickoff - timedelta(days=3)
        moments = [first + timedelta(hours=h) for h in (0, 20, 40, 60, 70, 71.5)]
        for step, moment in enumerate(moments):
            if moment >= NOW:
                break
            scan_id = f"s{fixture_id}-{step}"
            archive.start_scan(scan_id, moment, [moment.date().isoformat()])
            archive.record(scan_id, moment, [odds_obj(fixture_id, kickoff, 2.0 + 0.05 * (step % 3), 1.95 + 0.01 * step)])
    raw_day = archive.root / "raw" / (NOW - timedelta(days=10)).strftime("%Y-%m-%d")
    raw_day.mkdir(parents=True)
    (raw_day / "old-scan.jsonl.gz").write_bytes(gzip.compress(b'{"x": 1}'))
    recent_day = archive.root / "raw" / (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    recent_day.mkdir(parents=True)
    (recent_day / "new-scan.jsonl.gz").write_bytes(gzip.compress(b'{"x": 2}'))
    return archive


def table_rows(db, table, where="1"):
    with closing(sqlite3.connect(db)) as con:
        return con.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY 1, 2, 3").fetchall()


def counts_by_fixture(db, table):
    with closing(sqlite3.connect(db)) as con:
        return dict(con.execute(f"SELECT fixture_id, COUNT(*) FROM {table} GROUP BY fixture_id").fetchall())


@pytest.fixture
def odds(tmp_path):
    archive = build_odds_archive(tmp_path / "api-football-odds")
    return archive, tmp_path / "archive"


def read_export_rows(directory, table):
    manifest = ex.Manifest.read(directory)
    header, rows = ex.read_jsonl_gz(directory / manifest.tables[table].file)
    return header["columns"], [tuple(r) for r in rows]


def test_old_fixtures_leave_the_database_and_every_row_is_in_the_archive(odds):
    archive, archive_root = odds
    db = archive.db_path
    quotes_before = table_rows(db, "quotes")
    obs_before = table_rows(db, "observations")
    per_fixture = counts_by_fixture(db, "quotes")

    report = oret.run_retention(archive.root, archive_root, now=NOW, batch_fixtures=1)

    assert report.fixtures == 2 and len(report.exports) == 2
    remaining = counts_by_fixture(db, "quotes")
    assert set(remaining) == {201, 202, 301}
    assert set(counts_by_fixture(db, "observations")) == {201, 202, 301}
    assert set(counts_by_fixture(db, "quote_state")) == {201, 202, 301}
    assert len(table_rows(db, "fixtures")) == 5  # fixtures and scans stay
    assert report.quotes == per_fixture[101] + per_fixture[102]

    exported_quotes, exported_obs = [], []
    for directory in ex.final_exports(archive_root, "odds"):
        columns, rows = read_export_rows(directory, "quotes")
        assert tuple(columns) == oret.QUOTE_COLUMNS
        exported_quotes += rows
        exported_obs += read_export_rows(directory, "observations")[1]
        manifest = ex.Manifest.read(directory)
        for entry in manifest.tables.values():
            assert ex.sha256_file(directory / entry.file) == entry.sha256
            assert ex.count_rows(directory / entry.file) == entry.rows
    # row conservation: archived + remaining == before, and the archived rows are exactly the deleted ones
    with closing(sqlite3.connect(db)) as con:
        cols = ", ".join(oret.QUOTE_COLUMNS)
        remaining_quotes = con.execute(f"SELECT {cols} FROM quotes").fetchall()
        before_quotes = [r for r in quotes_before]
    assert len(exported_quotes) + len(remaining_quotes) == len(before_quotes)
    assert sorted(map(tuple, exported_quotes)) == sorted(r for r in (tuple(q) for q in before_quotes) if r[0] in (101, 102))
    assert len(exported_obs) + len(table_rows(db, "observations")) == len(obs_before)
    ledger = oret.ledger_rows(archive.root)
    assert [row["export_id"] for row in ledger] == report.exports
    assert sum(row["quotes"] for row in ledger) == report.quotes


def test_retention_is_idempotent(odds):
    archive, archive_root = odds
    first = oret.run_retention(archive.root, archive_root, now=NOW)
    files = sorted(p.relative_to(archive_root) for p in archive_root.rglob("*") if p.is_file())
    quotes = table_rows(archive.db_path, "quotes")
    second = oret.run_retention(archive.root, archive_root, now=NOW)
    assert first.fixtures == 2
    assert second.fixtures == 0 and second.exports == []
    assert sorted(p.relative_to(archive_root) for p in archive_root.rglob("*") if p.is_file()) == files
    assert table_rows(archive.db_path, "quotes") == quotes


def test_nothing_the_scanner_reads_inside_the_window_changes(odds):
    archive, archive_root = odds
    db = archive.db_path
    window = [201, 202]
    closing_before = markets.load_closing_quotes(db, window)
    current_before = markets.load_current_quotes(db, NOW)
    checks_before, info_before = bet_types.measure_bet_types(db, NOW)

    oret.run_retention(archive.root, archive_root, now=NOW)

    pd.testing.assert_frame_equal(markets.load_closing_quotes(db, window), closing_before)
    pd.testing.assert_frame_equal(markets.load_current_quotes(db, NOW), current_before)
    checks_after, info_after = bet_types.measure_bet_types(db, NOW)
    pd.testing.assert_frame_equal(checks_after, checks_before)
    assert info_after == info_before
    assert not closing_before.empty and set(closing_before["fixture_id"]) == set(window)


def test_keep_days_below_the_scanner_window_is_refused(odds):
    archive, archive_root = odds
    with pytest.raises(ValueError):
        oret.run_retention(archive.root, archive_root, now=NOW, keep_days=6)


def test_dry_run_changes_nothing(odds):
    archive, archive_root = odds
    before = table_rows(archive.db_path, "quotes")
    report = oret.run_retention(archive.root, archive_root, now=NOW, dry_run=True)
    assert report.fixtures == 2 and report.quotes > 0 and report.exports == []
    assert table_rows(archive.db_path, "quotes") == before
    assert not archive_root.exists() or not any(archive_root.rglob("*.gz"))
    assert (archive.root / "raw").exists() and any((archive.root / "raw").rglob("old-scan.jsonl.gz"))


def test_old_raw_days_move_to_the_archive(odds):
    archive, archive_root = odds
    report = oret.run_retention(archive.root, archive_root, now=NOW)
    old_day = (NOW - timedelta(days=10)).strftime("%Y-%m-%d")
    assert report.raw_days_moved == [old_day]
    assert (archive_root / "odds" / "raw" / old_day / "old-scan.jsonl.gz").exists()
    assert not (archive.root / "raw" / old_day).exists()
    assert (archive.root / "raw" / (NOW - timedelta(days=1)).strftime("%Y-%m-%d") / "new-scan.jsonl.gz").exists()


def test_a_failed_export_leaves_the_database_untouched_and_no_partial_directory(odds, monkeypatch):
    archive, archive_root = odds
    before = table_rows(archive.db_path, "quotes")

    def broken(self, expected):
        raise ValueError("disk said no")

    monkeypatch.setattr(ex.ExportWriter, "verify", broken)
    with pytest.raises(ValueError):
        oret.run_retention(archive.root, archive_root, now=NOW)
    assert table_rows(archive.db_path, "quotes") == before
    assert oret.ledger_rows(archive.root) == []
    assert ex.partial_exports(archive_root, "odds") == []
    assert ex.final_exports(archive_root, "odds") == []


def test_partial_exports_are_finalised_or_discarded_by_the_ledger(odds):
    archive, archive_root = odds
    report = oret.run_retention(archive.root, archive_root, now=NOW, batch_fixtures=1)
    committed = ex.final_exports(archive_root, "odds")[0]
    # simulate a crash after the commit but before the rename ...
    partial = committed.with_name(committed.name + ex.PARTIAL_SUFFIX)
    committed.rename(partial)
    (partial / ex.MANIFEST).unlink()
    # ... and a crash before the commit (no ledger row)
    orphan = committed.parent / ("odds-orphan-001" + ex.PARTIAL_SUFFIX)
    orphan.mkdir()
    (orphan / "quotes.jsonl.gz").write_bytes(b"junk")

    again = oret.run_retention(archive.root, archive_root, now=NOW)
    assert (again.recovered, again.discarded) == (1, 1)
    assert committed.exists() and (committed / ex.MANIFEST).exists()
    assert ex.Manifest.read(committed).export_id == report.exports[0]
    assert not orphan.exists() and not partial.exists()


def test_snapshot_rows_are_change_only_along_open_hours_close():
    kickoff = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    quotes = [
        (2.00, kickoff - timedelta(hours=80)),  # open
        (2.10, kickoff - timedelta(hours=30)),  # in effect at h24
        (2.10, kickoff - timedelta(hours=10)),  # same price again: no new row
        (2.05, kickoff - timedelta(minutes=30)),  # close
        (9.99, kickoff + timedelta(minutes=5)),  # after kickoff: ignored
    ]
    rows = oret.snapshot_rows(quotes, kickoff)
    assert [(point, odd) for point, odd, _ in rows] == [(0, 2.00), (3, 2.10), (9, 2.05)]
    assert rows[0][2] == int((kickoff - timedelta(hours=80)).timestamp())
    assert oret.snapshot_rows([(1.5, kickoff + timedelta(hours=1))], kickoff) == []
    late = oret.snapshot_rows([(1.8, kickoff - timedelta(hours=2, minutes=30))], kickoff)
    assert late == [(0, 1.8, int((kickoff - timedelta(hours=2, minutes=30)).timestamp()))]


def test_snapshots_keep_opening_and_closing_prices_of_archived_fixtures(odds):
    archive, archive_root = odds
    last_sql = """
        SELECT q.bookmaker_id, q.bet_id, q.selection, q.odd FROM quotes q JOIN fixtures f USING (fixture_id)
        WHERE q.fixture_id = 101 AND q.rowid = (
            SELECT q2.rowid FROM quotes q2 WHERE q2.fixture_id = q.fixture_id AND q2.bookmaker_id = q.bookmaker_id
            AND q2.bet_id = q.bet_id AND q2.selection = q.selection AND q2.fetched_at < f.kickoff_utc
            ORDER BY q2.fetched_at {order}, q2.rowid {order} LIMIT 1)
    """
    with closing(sqlite3.connect(archive.db_path)) as con:
        expected_close = sorted(con.execute(last_sql.format(order="DESC")).fetchall())
        expected_open = sorted(con.execute(last_sql.format(order="ASC")).fetchall())
    oret.run_retention(archive.root, archive_root, now=NOW)
    with closing(sqlite3.connect(archive.root / oret.SNAPSHOT_DB_NAME)) as con:
        got_close = sorted(con.execute("SELECT bookmaker_id, bet_id, selection, odd FROM closing_quotes WHERE fixture_id = 101").fetchall())
        got_open = sorted(con.execute("SELECT bookmaker_id, bet_id, selection, odd FROM opening_quotes WHERE fixture_id = 101").fetchall())
        fixtures = con.execute("SELECT fixture_id, n_keys FROM snapshot_fixtures ORDER BY 1").fetchall()
    assert len(expected_close) == 15  # 3 bookmakers x 5 selections
    assert got_close == expected_close
    assert got_open == expected_open
    assert fixtures == [(101, 15), (102, 15)]


def test_snapshot_pruning_keeps_recent_fixtures(odds):
    archive, archive_root = odds
    oret.run_retention(archive.root, archive_root, now=NOW)
    pruned = oret.prune_snapshots(archive.root / oret.SNAPSHOT_DB_NAME, NOW - timedelta(days=10))
    assert pruned == 1  # fixture 101 kicked off 12 days ago; 102 nine days ago stays
    with closing(sqlite3.connect(archive.root / oret.SNAPSHOT_DB_NAME)) as con:
        assert [r[0] for r in con.execute("SELECT DISTINCT fixture_id FROM quote_snapshots")] == [102]


# --------------------------------------------------------------------------- #
# Synthetic scanner register
# --------------------------------------------------------------------------- #

LEG_COLUMNS = ["scan_id", "as_of", "leg_key", "bookmaker", "config_id", "fixture_id", "kickoff_utc", "market", "period", "line",
               "selection", "odd", "is_value", "is_model_only_value", "leg_class", "value_t088", "value_t100"]  # fmt: skip


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_register(path):
    register = ScannerRegister(path)
    old = NOW - timedelta(days=10)
    recent = NOW - timedelta(days=2)
    rows = []

    def leg(scan, key, book, kickoff, cls, *, value=0, model_value=0, t088=0, t100=0):
        rows.append((scan, iso(kickoff - timedelta(hours=5)), key, book, "cfg", 1 if kickoff == old else 2, iso(kickoff), "1x2",
                     "ft", None, "home", 2.1, value, model_value, cls, t088, t100))  # fmt: skip

    leg("sc1", "k-near-settled", "Bet365", old, "near_miss")  # archived
    leg("sc1", "k-suspect-settled", "1xBet", old, "suspect")  # archived
    leg("sc1", "k-near-unsettled", "Bet365", old, "near_miss")  # kept: no settlement, within grace
    leg("sc1", "k-value", "Bet365", old, "value", value=1, t100=1)  # kept: value
    leg("sc1", "k-regime-only", "Bet365", old, "near_miss", t088=1)  # kept: value at a regime
    leg("sc1", "k-model-value", "Bet365", old, "model_only", model_value=1)  # kept
    leg("sc1", "k-coupon", "1xBet", old, "near_miss")  # kept: coupon leg
    leg("sc1", "k-clv", "Bet365", old, "near_miss")  # kept: has a CLV row
    leg("sc2", "k-recent", "Bet365", recent, "near_miss")  # kept: recent
    with closing(sqlite3.connect(path)) as con:
        for column in ("value_t088", "value_t100"):  # regime columns are added by the register on first use
            con.execute(f"ALTER TABLE legs ADD COLUMN {column} INTEGER")
        con.executemany(f"INSERT INTO legs ({', '.join(LEG_COLUMNS)}) VALUES ({', '.join('?' * len(LEG_COLUMNS))})", rows)
        con.execute("INSERT INTO coupon_legs (scan_id, coupon_id, leg_no, leg_key, bookmaker, fixture_id) VALUES ('sc1', 'c1', 1, 'k-coupon', '1xBet', 1)")
        con.execute(
            "INSERT INTO clv (scan_id, bookmaker, leg_key, computed_at, odd_taken) VALUES ('sc1', 'Bet365', 'k-clv', ?, 2.1)", (iso(old),)
        )
        for key in ("k-near-settled", "k-suspect-settled", "k-value", "k-coupon", "k-clv", "k-regime-only", "k-model-value"):
            con.execute(
                "INSERT INTO settlements (leg_key, fixture_id, outcome, settled_at, source) VALUES (?, 1, 'loss', ?, 'test')", (key, iso(old))
            )
        con.commit()
    return register


def test_register_retention_archives_only_settled_non_value_legs(tmp_path):
    path = tmp_path / "scanner.sqlite"
    register = build_register(path)
    paper_before = register.paper_legs()
    unsettled_before = register.unsettled_legs()
    settlements_before = register.table("settlements")
    clv_before = register.table("clv")
    legs_before = register.table("legs")

    report = rret.run_register_retention(path, tmp_path / "archive", now=NOW, backup=False)

    assert report.legs_archived == 2
    remaining = set(register.table("legs")["leg_key"])
    assert remaining == {"k-near-unsettled", "k-value", "k-regime-only", "k-model-value", "k-coupon", "k-clv", "k-recent"}
    pd.testing.assert_frame_equal(register.paper_legs(), paper_before)
    pd.testing.assert_frame_equal(register.unsettled_legs(), unsettled_before)
    pd.testing.assert_frame_equal(register.table("settlements"), settlements_before)
    pd.testing.assert_frame_equal(register.table("clv"), clv_before)
    directory = ex.final_exports(tmp_path / "archive", "scanner")[0]
    columns, rows = read_export_rows(directory, "legs")
    assert sorted(r[columns.index("leg_key")] for r in rows) == ["k-near-settled", "k-suspect-settled"]
    assert len(rows) + len(register.table("legs")) == len(legs_before)  # row conservation
    # the append-only guard is back
    with closing(sqlite3.connect(path)) as con, pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM legs WHERE leg_key = 'k-recent'")


def test_register_retention_is_idempotent_and_grace_releases_unsettled_legs(tmp_path):
    path = tmp_path / "scanner.sqlite"
    register = build_register(path)
    rret.run_register_retention(path, tmp_path / "archive", now=NOW, backup=False)
    again = rret.run_register_retention(path, tmp_path / "archive", now=NOW, backup=False)
    assert again.legs_archived == 0 and again.exports == []
    later = rret.run_register_retention(path, tmp_path / "archive", now=NOW + timedelta(days=25), backup=False)
    assert later.legs_archived == 1  # k-near-unsettled is past the 30-day grace; k-recent (unsettled) is not yet
    assert set(register.table("legs")["leg_key"]) == {"k-value", "k-regime-only", "k-model-value", "k-coupon", "k-clv", "k-recent"}


def test_register_backup_once_per_week(tmp_path):
    path = tmp_path / "scanner.sqlite"
    build_register(path)
    first = rret.weekly_backup(path, tmp_path / "archive", NOW)
    assert first is not None and first.name.startswith("scanner-2026W")
    assert rret.weekly_backup(path, tmp_path / "archive", NOW + timedelta(hours=1)) is None
    restored = tmp_path / "restored.sqlite"
    restored.write_bytes(gzip.decompress(first.read_bytes()))
    with closing(sqlite3.connect(restored)) as con:
        assert con.execute("SELECT COUNT(*) FROM legs").fetchone()[0] == 9


def test_forecast_log_trim_keeps_recent_rows(tmp_path):
    from model_buk.scanner.pipeline import ForecastLog

    path = tmp_path / "scanner.sqlite"
    build_register(path)
    log_path = tmp_path / "model_forecasts.sqlite"
    ForecastLog(log_path)
    with closing(sqlite3.connect(log_path)) as con:
        for fixture, kickoff in ((1, NOW - timedelta(days=40)), (2, NOW - timedelta(days=2))):
            con.execute(
                "INSERT INTO model_forecasts VALUES ('2026-09-01T00:00:00Z', ?, '1x2', 'ft', 'na', NULL, 'home', 'pool_models', 0.5, ?, 's', 'x')",
                (fixture, iso(kickoff)),
            )
        con.commit()
    report = rret.run_register_retention(path, tmp_path / "archive", now=NOW, forecast_log=log_path, forecasts_keep_days=30, backup=False)
    assert report.forecasts_archived == 1
    with closing(sqlite3.connect(log_path)) as con:
        assert [r[0] for r in con.execute("SELECT fixture_id FROM model_forecasts")] == [2]
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("DELETE FROM model_forecasts")


def test_export_files_are_gzip_jsonl_with_a_header(tmp_path):
    writer = ex.ExportWriter(tmp_path, "odds", "odds-test-001", NOW)
    writer.write_table("t", ["a", "b"], [(1, "x"), (None, 2.5)])
    writer.verify({"t": 2})
    final = writer.finalize({"note": "n"})
    lines = gzip.decompress((final / "t.jsonl.gz").read_bytes()).decode().splitlines()
    assert json.loads(lines[0])["columns"] == ["a", "b"]
    assert [json.loads(line) for line in lines[1:]] == [[1, "x"], [None, 2.5]]
    manifest = ex.Manifest.read(final)
    assert manifest.meta == {"note": "n"} and manifest.tables["t"].rows == 2
    with pytest.raises(ValueError):
        writer2 = ex.ExportWriter(tmp_path, "odds", "odds-test-002", NOW)
        writer2.write_table("t", ["a"], [(1, 2)])
