"""Archive upload to the bucket and pull/merge back to the PC (model_buk.maintenance.bucket_sync), fake S3 client."""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from model_buk.maintenance import bucket_sync as bs
from model_buk.maintenance import exports as ex
from model_buk.maintenance import odds_retention as oret
from model_buk.maintenance import register_retention as rret

from test_maintenance_retention import NOW, build_odds_archive, build_register

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ENV = {
    "AWS_S3_BUCKET_NAME": "model-buk-archive-abc",
    "AWS_ENDPOINT_URL": "https://t3.storageapi.dev",
    "AWS_ACCESS_KEY_ID": "test-key-id",
    "AWS_SECRET_ACCESS_KEY": "test-secret",
    "AWS_DEFAULT_REGION": "auto",
    "AWS_S3_URL_STYLE": "virtual",
}


class NotFound(Exception):
    def __init__(self, key):
        super().__init__(key)
        self.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    def __init__(self, page_size=3, fail_keys=()):
        self.objects: dict[tuple[str, str], tuple[bytes, dict]] = {}
        self.puts: list[str] = []
        self.page_size = page_size
        self.fail_keys = set(fail_keys)

    def put_object(self, *, Bucket, Key, Body, ContentLength=None, Metadata=None):
        if Key in self.fail_keys:
            raise ConnectionError("simulated network failure")
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        assert ContentLength is None or ContentLength == len(data)
        self.objects[(Bucket, Key)] = (data, dict(Metadata or {}))
        self.puts.append(Key)

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise NotFound(Key)
        data, meta = self.objects[(Bucket, Key)]
        return {"ContentLength": len(data), "Metadata": meta}

    def get_object(self, *, Bucket, Key):
        data, meta = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data), "ContentLength": len(data), "Metadata": meta}

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        return {
            "Contents": [{"Key": k, "Size": len(self.objects[(Bucket, k)][0])} for k in page],
            "IsTruncated": more,
            "NextContinuationToken": str(start + self.page_size) if more else None,
        }


@pytest.fixture
def config():
    return bs.BucketConfig.from_env(ENV)


@pytest.fixture
def server(tmp_path):
    """A server data root after one retention run: odds and register exports, a raw day, a backup."""
    root = tmp_path / "server"
    archive = build_odds_archive(root / "api-football-odds")
    original = tmp_path / "odds_before.sqlite3"
    shutil.copy2(archive.db_path, original)
    oret.run_retention(archive.root, root / "archive", now=NOW, batch_fixtures=1)
    build_register(root / "scanner" / "scanner.sqlite")
    rret.run_register_retention(root / "scanner" / "scanner.sqlite", root / "archive", now=NOW)
    partial = root / "archive" / "odds" / "exports" / "2026-10" / ("odds-x-001" + ex.PARTIAL_SUFFIX)
    partial.mkdir(parents=True)
    (partial / "quotes.jsonl.gz").write_bytes(b"not final")
    (root / "archive" / "odds" / "half.tmp").write_bytes(b"temp")
    return root, original


def test_config_reads_the_railway_variable_names(monkeypatch):
    config = bs.BucketConfig.from_env(ENV, prefix="x")
    assert (config.bucket, config.endpoint, config.region, config.url_style, config.prefix) == (
        "model-buk-archive-abc", "https://t3.storageapi.dev", "auto", "virtual", "x/",
    )  # fmt: skip
    plain = bs.BucketConfig.from_env(
        {"BUCKET": "b", "ENDPOINT": "https://e", "ACCESS_KEY_ID": "k", "SECRET_ACCESS_KEY": "s", "REGION": "auto"}
    )
    assert plain.bucket == "b" and plain.access_key == "k" and plain.url_style == "virtual"
    assert "test-secret" not in bs.BucketConfig.from_env(ENV).describe()
    assert "test-key-id" not in bs.BucketConfig.from_env(ENV).describe()
    with pytest.raises(RuntimeError, match="AWS_SECRET_ACCESS_KEY"):
        bs.BucketConfig.from_env({"AWS_S3_BUCKET_NAME": "b", "AWS_ACCESS_KEY_ID": "k"})
    with pytest.raises(RuntimeError):
        bs.BucketConfig.from_env({**ENV, "AWS_S3_URL_STYLE": "sideways"})


def test_upload_sends_every_final_file_once_and_verifies_it(server, config):
    root, _ = server
    s3 = FakeS3()
    report = bs.upload_archive(root / "archive", s3, config)
    assert report.failed == [] and report.uploaded == report.files > 0
    keys = set(s3.puts)
    assert all(k.startswith("archive/") for k in keys)
    assert not any(".partial" in k or k.endswith(".tmp") or "/." in k for k in keys)
    assert any(k.startswith("archive/odds/exports/") and k.endswith("manifest.json") for k in keys)
    assert any(k.startswith("archive/odds/raw/") for k in keys)
    assert any(k.startswith("archive/scanner/backups/scanner-") for k in keys)
    for (_, key), (data, meta) in s3.objects.items():
        local = root / "archive" / key[len("archive/") :]
        assert local.read_bytes() == data and meta["sha256"] == ex.sha256_file(local)

    again = bs.upload_archive(root / "archive", s3, config)
    assert again.uploaded == 0 and again.unchanged == report.files and len(s3.puts) == len(keys)


def test_files_already_in_the_bucket_are_not_uploaded_again(server, config):
    root, _ = server
    s3 = FakeS3()
    bs.upload_archive(root / "archive", s3, config)
    (root / "archive" / bs.STATE_DB).unlink()  # state lost: the HEAD check still avoids the upload
    puts = len(s3.puts)
    report = bs.upload_archive(root / "archive", s3, config)
    assert report.uploaded == 0 and report.already_remote == report.files and len(s3.puts) == puts


def test_local_files_are_pruned_only_after_a_verified_upload(server, config):
    root, _ = server
    s3 = FakeS3(fail_keys={"archive/scanner/backups/" + next((root / "archive" / "scanner" / "backups").iterdir()).name})
    first = bs.upload_archive(root / "archive", s3, config, keep_local_days=14)
    assert len(first.failed) == 1 and first.pruned == 0
    # one exported object goes missing from the bucket before the prune
    manifest_key = next(k for k in s3.puts if k.endswith("manifest.json"))
    del s3.objects[(config.bucket, manifest_key)]
    for path in (root / "archive").rglob("*"):
        if path.is_file():
            stamp = (ex.utc_now() - timedelta(days=30)).timestamp()
            os.utime(path, (stamp, stamp))
    # the state remembered (size, mtime); utime changed mtime, so re-upload happens first, then prune
    later = bs.upload_archive(root / "archive", s3, config, keep_local_days=14)
    assert later.failed and later.failed[0].startswith("scanner/backups/")  # still failing: never pruned
    backup = next((root / "archive" / "scanner" / "backups").iterdir())
    assert backup.exists()
    assert (config.bucket, manifest_key) in s3.objects  # re-uploaded because its HEAD no longer matched
    remaining = [p for p in (root / "archive").rglob("*") if p.is_file() and not p.name.startswith(".")]
    final = [p for p in remaining if ".partial" not in str(p) and not p.name.endswith(".tmp")]
    assert final == [backup]  # everything else was verified in the bucket and deleted locally


def test_pull_downloads_and_merges_without_duplicates(server, config, tmp_path):
    root, original = server
    s3 = FakeS3(page_size=2)
    bs.upload_archive(root / "archive", s3, config)

    pc = tmp_path / "pc"
    (pc / "api-football-odds").mkdir(parents=True)
    shutil.copy2(original, pc / "api-football-odds" / "odds.sqlite3")  # the PC already has the full history
    with closing(sqlite3.connect(original)) as con:
        quotes_before = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        observations_before = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    report = bs.pull_archive(pc / "archive", s3, config)
    assert report.failed == [] and report.downloaded == len(s3.objects) and report.remote == len(s3.objects)
    merged = bs.merge_exports(pc / "archive", odds_root=pc / "api-football-odds")
    assert merged == {"odds": 2, "scanner": 1}
    with closing(sqlite3.connect(pc / "api-football-odds" / "odds.sqlite3")) as con:
        assert con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == quotes_before  # overlap skipped
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == observations_before
        assert con.execute("SELECT COUNT(*) FROM archive_imports").fetchone()[0] == 2
    with closing(sqlite3.connect(pc / "api-football-odds" / oret.SNAPSHOT_DB_NAME)) as con:
        assert {r[0] for r in con.execute("SELECT DISTINCT fixture_id FROM quote_snapshots")} == {101, 102}
    with closing(sqlite3.connect(pc / "archive" / "scanner" / "register_archive.sqlite")) as con:
        assert sorted(r[0] for r in con.execute("SELECT leg_key FROM legs")) == ["k-near-settled", "k-suspect-settled"]

    # a second pull and merge change nothing
    assert bs.pull_archive(pc / "archive", s3, config).downloaded == 0
    assert bs.merge_exports(pc / "archive", odds_root=pc / "api-football-odds") == {}


def test_merge_into_an_empty_pc_archive_restores_every_exported_row(server, config, tmp_path):
    root, original = server
    s3 = FakeS3()
    bs.upload_archive(root / "archive", s3, config)
    pc = tmp_path / "pc2"
    bs.pull_archive(pc / "archive", s3, config)
    bs.merge_exports(pc / "archive", odds_root=pc / "api-football-odds")
    with closing(sqlite3.connect(original)) as con:
        expected = sorted(con.execute(f"SELECT {', '.join(oret.QUOTE_COLUMNS)} FROM quotes WHERE fixture_id IN (101, 102)").fetchall())
    with closing(sqlite3.connect(pc / "api-football-odds" / "odds.sqlite3")) as con:
        got = sorted(con.execute(f"SELECT {', '.join(oret.QUOTE_COLUMNS)} FROM quotes").fetchall())
    assert got == expected


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scripts_run_with_a_fake_client(server, tmp_path, monkeypatch, capsys):
    root, _ = server
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    s3 = FakeS3()
    upload = _load_script("archive_to_bucket")
    assert upload.main(["--data-root", str(root)], client=s3) == 0
    line = capsys.readouterr().out
    assert "uploaded=" in line and "test-secret" not in line and "test-key-id" not in line
    pull = _load_script("pull_archive")
    assert pull.main(["--data-root", str(tmp_path / "pc3")], client=s3) == 0
    assert "merged: odds=2 scanner=1" in capsys.readouterr().out
    for name in ENV:
        monkeypatch.delenv(name)
    monkeypatch.setattr(bs, "_user_env_windows", lambda name: None)
    assert upload.main(["--data-root", str(root)], client=s3) == 2
