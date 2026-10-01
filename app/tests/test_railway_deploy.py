"""The Railway container pieces: entry point (auth, pages, seed), crontab and job wrappers (deploy/railway)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy" / "railway"
JOBS = DEPLOY / "jobs"
SCRIPTS = REPO / "app" / "scripts"
CONTAINER_JOBS = "/opt/model-buk/deploy/railway/jobs/"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses with postponed annotations need the module registered
    spec.loader.exec_module(module)
    return module


ep = load(DEPLOY / "entrypoint.py", "railway_entrypoint")


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# --------------------------------------------------------------------------- #
# Authentication and URL mapping
# --------------------------------------------------------------------------- #


def test_basic_auth_accepts_only_the_configured_credentials():
    assert ep.check_basic_auth(basic("oskar", "s3cret pass"), "oskar", "s3cret pass")
    assert ep.check_basic_auth("basic " + basic("oskar", "a:b").split()[1], "oskar", "a:b")  # colon inside the password
    assert not ep.check_basic_auth(basic("oskar", "wrong"), "oskar", "s3cret pass")
    assert not ep.check_basic_auth(basic("other", "s3cret pass"), "oskar", "s3cret pass")
    assert not ep.check_basic_auth(None, "oskar", "s3cret pass")
    assert not ep.check_basic_auth("Bearer abc", "oskar", "s3cret pass")
    assert not ep.check_basic_auth("Basic !!!not-base64!!!", "oskar", "s3cret pass")
    assert not ep.check_basic_auth("Basic " + base64.b64encode(b"no-colon").decode(), "oskar", "x")
    assert not ep.check_basic_auth("Basic " + base64.b64encode(b"\xff\xfe:x").decode(), "oskar", "x")


def test_no_password_means_nothing_is_ever_authorised():
    for password in (None, ""):
        assert not ep.check_basic_auth(basic("model-buk", ""), "model-buk", password)
    settings = ep.Settings.from_env({"SCANNER_PAGE_PASSWORD": "   "})
    assert settings.password is None and settings.user == ep.DEFAULT_USER
    settings = ep.Settings.from_env({"SCANNER_PAGE_PASSWORD": "pw\r\n", "SCANNER_PAGE_USER": " oskar "})
    assert settings.password == "pw" and settings.user == "oskar"  # a value piped from PowerShell keeps no newline


def test_page_urls_map_only_to_scanner_outputs(tmp_path):
    scanner = tmp_path / "scanner"
    assert ep.resolve_page(tmp_path, "/") == scanner / "latest.html"
    assert ep.resolve_page(tmp_path, "/latest.html?x=1") == scanner / "latest.html"
    assert ep.resolve_page(tmp_path, "/html/2026-10-01.html") == scanner / "html" / "2026-10-01.html"
    assert ep.resolve_page(tmp_path, "/csv/2026-10-01_coupons.csv") == scanner / "csv" / "2026-10-01_coupons.csv"
    assert ep.resolve_page(tmp_path, "/latest_coupons.csv") == scanner / "latest_coupons.csv"
    for bad in (
        "/html/../scanner.sqlite",
        "/html/%2e%2e%2fsecret.html",
        "/html/..%2f..%2fetc.html",
        "/html/.hidden.html",
        "/html/x.sqlite",
        "/csv/../../logs/a.csv",
        "/scanner.sqlite",
        "/logs/scanner.log",
        "/html/",
        "/../../etc/passwd",
    ):
        assert ep.resolve_page(tmp_path, bad) is None, bad


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #


@pytest.fixture
def served(tmp_path):
    def start(password: str | None):
        data = tmp_path / "data"
        (data / "scanner" / "html").mkdir(parents=True, exist_ok=True)
        (data / "logs").mkdir(exist_ok=True)
        (data / "scanner" / "latest.html").write_text("<p>value legs</p>", encoding="utf-8")
        (data / "scanner" / "html" / "2026-10-01.html").write_text("<p>day</p>", encoding="utf-8")
        (data / "scanner" / "scanner.sqlite").write_bytes(b"secret register")
        (data / "logs" / "scanner.log").write_text("[2026-10-01 18:15:40] exit=0 dur=50s scan=x\n", encoding="utf-8")
        env = {"MODEL_BUK_DATA": str(data), "SCANNER_PAGE_USER": "oskar", "PORT": "0"}
        if password is not None:
            env["SCANNER_PAGE_PASSWORD"] = password
        state = ep.State(ep.Settings.from_env(env))
        state.phase = "running"
        server = ep.ThreadingHTTPServer(("127.0.0.1", 0), ep.make_handler(state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    servers: list = []
    yield start
    for server in servers:
        server.shutdown()


def get(url: str, auth: str | None = None, method: str = "GET"):
    request = urllib.request.Request(url, method=method, headers={"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_pages_need_the_password_and_healthz_does_not(served):
    base = served("long random password")
    status, _, body = get(base + "/healthz")
    assert status == 200 and json.loads(body)["status"] in ("ok", "cron_down")
    status, headers, _ = get(base + "/")
    assert status == 401 and headers["WWW-Authenticate"].startswith("Basic")
    assert get(base + "/", basic("oskar", "wrong"))[0] == 401
    status, headers, body = get(base + "/", basic("oskar", "long random password"))
    assert status == 200 and body == b"<p>value legs</p>" and headers["Cache-Control"] == "no-store"
    assert get(base + "/html/2026-10-01.html", basic("oskar", "long random password"))[2] == b"<p>day</p>"
    assert b"2026-10-01.html" in get(base + "/html/", basic("oskar", "long random password"))[2]
    assert get(base + "/scanner.sqlite", basic("oskar", "long random password"))[0] == 404
    assert get(base + "/html/..%2fscanner.sqlite", basic("oskar", "long random password"))[0] == 404
    status, _, body = get(base + "/status", basic("oskar", "long random password"))
    assert status == 200 and b"[scanner] [2026-10-01 18:15:40] exit=0" in body
    assert get(base + "/", basic("oskar", "long random password"), method="POST")[0] == 405


def test_without_a_password_every_page_is_refused(served):
    base = served(None)
    assert get(base + "/healthz")[0] == 200
    for path in ("/", "/latest.html", "/status", "/html/2026-10-01.html"):
        status, _, body = get(base + path, basic("oskar", ""))
        assert status == 503 and b"SCANNER_PAGE_PASSWORD" in body


# --------------------------------------------------------------------------- #
# Seed archive
# --------------------------------------------------------------------------- #


def make_seed(root: Path, members: dict[str, bytes], *, checksum: bool = True, bad_member: str | None = None) -> Path:
    archive = root / ep.SEED_NAME
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if bad_member:
            info = tarfile.TarInfo(bad_member)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    if checksum:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (root / (ep.SEED_NAME + ".sha256")).write_text(f"{digest}  seed.tar.gz\n", encoding="utf-8")
    return archive


def test_seed_is_used_only_when_complete_and_is_extracted_safely(tmp_path):
    make_seed(tmp_path, {"scanner/scanner.sqlite": b"register", "api-football-odds/odds.sqlite3": b"odds"}, checksum=False)
    assert not ep.seed_ready(tmp_path)  # no checksum yet: the upload may still be running
    (tmp_path / (ep.SEED_NAME + ".sha256")).write_text("0" * 64, encoding="utf-8")
    assert not ep.seed_ready(tmp_path)  # wrong checksum (truncated upload)
    make_seed(tmp_path, {"scanner/scanner.sqlite": b"register", "api-football-odds/odds.sqlite3": b"odds"})
    assert ep.seed_ready(tmp_path)
    marker = ep.extract_seed(tmp_path, log=lambda _t: None)
    assert (tmp_path / "scanner" / "scanner.sqlite").read_bytes() == b"register"
    assert marker["entries"] == 2 and (tmp_path / ep.SEEDED_MARKER).exists()
    assert not (tmp_path / ep.SEED_NAME).exists() and not (tmp_path / (ep.SEED_NAME + ".sha256")).exists()


def test_a_seed_with_a_path_outside_the_volume_is_refused(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    make_seed(root, {"ok.txt": b"1"}, bad_member="../escape.txt")
    assert ep.seed_ready(root)
    with pytest.raises(tarfile.TarError):
        ep.extract_seed(root, log=lambda _t: None)
    assert not (tmp_path / "escape.txt").exists() and not (root / ep.SEEDED_MARKER).exists()


def test_wait_for_seed_returns_at_once_when_seeded_or_allowed(tmp_path):
    state = ep.State(ep.Settings.from_env({"MODEL_BUK_DATA": str(tmp_path), "ALLOW_EMPTY_DATA": "1"}))
    assert ep.wait_for_seed(state)
    (tmp_path / "seeded").mkdir()
    (tmp_path / "seeded" / ep.SEEDED_MARKER).write_text("{}", encoding="utf-8")
    state = ep.State(ep.Settings.from_env({"MODEL_BUK_DATA": str(tmp_path / "seeded")}))
    assert ep.wait_for_seed(state)
    empty = ep.State(ep.Settings.from_env({"MODEL_BUK_DATA": str(tmp_path / "empty")}))
    empty.stopping.set()
    assert not ep.wait_for_seed(empty) and empty.phase == "waiting_for_seed"
    assert empty.health()["status"] == "waiting_for_seed"


def test_wait_for_seed_extracts_a_complete_upload(tmp_path):
    make_seed(tmp_path, {"scanner/latest.html": b"<p>x</p>"})
    state = ep.State(ep.Settings.from_env({"MODEL_BUK_DATA": str(tmp_path)}))
    assert ep.wait_for_seed(state)
    assert (tmp_path / ep.SEEDED_MARKER).exists() and (tmp_path / "scanner" / "latest.html").exists()


# --------------------------------------------------------------------------- #
# Crontab and job wrappers
# --------------------------------------------------------------------------- #

FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def cron_values(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            assert step >= 1
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
        else:
            start = end = int(part)
        assert low <= start <= end <= high, field
        values.update(range(start, end + 1, step))
    return values


def crontab_jobs() -> list[tuple[list[set[int]], str]]:
    jobs = []
    for raw in (DEPLOY / "crontab").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Z_]+=", line):
            continue
        fields = line.split(None, 5)
        assert len(fields) == 6, line
        schedule = [cron_values(f, lo, hi) for f, (lo, hi) in zip(fields[:5], FIELD_RANGES, strict=True)]
        jobs.append((schedule, fields[5]))
    return jobs


def test_crontab_parses_and_runs_every_wrapper_in_polish_time():
    text = (DEPLOY / "crontab").read_text(encoding="utf-8")
    assert re.search(r"^CRON_TZ=Europe/Warsaw$", text, re.M)
    jobs = crontab_jobs()
    commands = [command for _, command in jobs]
    assert len(commands) == len(set(commands))
    scheduled = set()
    for _, command in jobs:
        assert command.startswith(CONTAINER_JOBS) and command.endswith(".sh"), command
        script = JOBS / command[len(CONTAINER_JOBS) :]
        assert script.exists(), script
        scheduled.add(script.name)
    wrappers = {p.name for p in JOBS.glob("*.sh")} - {"run_job.sh"}
    assert scheduled == wrappers  # every wrapper is scheduled, nothing points to a missing file
    by_name = {Path(c).stem: s for s, c in jobs}
    assert by_name["scanner"][0] == {15, 45} and by_name["odds_collector"][0] == {5}
    assert by_name["closing_odds"][0] == {12, 27, 42, 57} and len(by_name["prematch_collector"][0]) == 3
    # nightly order: season sweep -> history build -> model fit -> retention -> upload
    order = ["refresh_season", "history_build", "fit_live_models", "compact_odds", "compact_register", "archive_upload"]
    times = [min(by_name[n][1]) * 60 + min(by_name[n][0]) for n in order]
    assert times == sorted(times) and all(len(by_name[n][1]) == 1 for n in order)


def wrapper_commands(script: Path) -> list[list[str]]:
    """The ``python scripts/<x>.py ...`` invocations of a wrapper, with the data root substituted."""
    text = script.read_text(encoding="utf-8")
    exec_line = next(line for line in text.splitlines() if line.startswith("exec "))
    commands = []
    for match in re.finditer(r"python (scripts/[\w]+\.py)([^&']*)", exec_line):
        args = shlex.split(match.group(2).replace('"$D', '"/data').replace('"$0', '"/data'))
        commands.append([match.group(1), *args])
    return commands


class Parsed(Exception):
    pass


@pytest.mark.parametrize("wrapper", sorted(p.name for p in JOBS.glob("*.sh") if p.name != "run_job.sh"))
def test_every_wrapper_calls_an_existing_script_with_valid_arguments(wrapper, monkeypatch):
    script = JOBS / wrapper
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n") and "\r" not in text
    exec_line = next(line for line in text.splitlines() if line.startswith("exec "))
    assert '"$HERE/run_job.sh" ' + script.stem + " " in exec_line  # log file named after the wrapper
    timeout = int(exec_line.split()[3])
    assert 60 <= timeout <= 3600
    commands = wrapper_commands(script)
    assert commands, wrapper
    original = argparse.ArgumentParser.parse_args

    def parse_only(self, args=None, namespace=None):
        raise Parsed(original(self, args, namespace))

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_only)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for command in commands:
        path = SCRIPTS / Path(command[0]).name
        assert path.exists(), path
        module = load(path, f"job_{path.stem}")
        monkeypatch.setattr(sys, "argv", [str(path), *command[1:]])
        with pytest.raises(Parsed):
            module.main()


def test_job_wrappers_and_crontab_use_lf_line_endings():
    for path in [*JOBS.glob("*.sh"), DEPLOY / "crontab"]:
        assert b"\r\n" not in path.read_bytes(), path
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8") if (REPO / ".gitattributes").exists() else ""
    attributes += (DEPLOY / ".gitattributes").read_text(encoding="utf-8") if (DEPLOY / ".gitattributes").exists() else ""
    assert "eol=lf" in attributes


def _bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(candidate).exists():
            return candidate
    return None


@pytest.mark.skipif(_bash() is None, reason="needs bash")
def test_run_job_writes_one_log_line_and_honours_timeout_and_budget(tmp_path):
    bash = _bash()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("flock", "python3"):  # flock is Linux-only; the stub grants every lock
        stub = stubs / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    data = tmp_path / "data"
    env = {
        **os.environ,
        "PATH": f"{stubs.as_posix()}{os.pathsep if os.name != 'nt' else ':'}" + os.environ.get("PATH", ""),
        "MODEL_BUK_DATA": data.as_posix(),
        "APP_DIR": tmp_path.as_posix(),
    }
    if os.name == "nt":  # Git Bash: give it a POSIX PATH with the stubs first
        env["PATH"] = "/" + stubs.as_posix().replace(":", "", 1) + ":/usr/bin:/bin"
    run_job = (JOBS / "run_job.sh").as_posix()

    def run(*args, extra_env=None):
        return subprocess.run([bash, run_job, *args], env={**env, **(extra_env or {})}, capture_output=True, text=True, timeout=60)

    result = run("demo", "30", "--shared", "odds", "--", "bash", "-c", "echo first line; echo; echo '  second  '; exit 3")
    assert result.returncode == 3, result.stderr
    lines = (data / "logs" / "demo.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and re.match(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] exit=3 dur=\d+s first line \| second$", lines[0])
    assert "demo [" in result.stdout

    result = run("slow", "1", "--", "sleep", "5")
    assert result.returncode == 124
    assert "killed: timeout after 1s" in (data / "logs" / "slow.log").read_text(encoding="utf-8")

    usage = next((data / "logs").glob("usage-*.tsv"))
    usage.write_text(usage.read_text(encoding="utf-8") + "00:00:00\tother\t500\t0\n", encoding="utf-8")
    result = run("demo", "30", "--", "echo", "should not run", extra_env={"DAILY_JOB_BUDGET_SECONDS": "100"})
    assert result.returncode == 75
    assert "skipped: daily job budget used" in (data / "logs" / "demo.log").read_text(encoding="utf-8").splitlines()[-1]
    assert run("bad", "30", "--nope", "x", "--", "true").returncode == 64


def test_railway_config_points_at_the_dockerfile_and_healthcheck():
    config = json.loads((DEPLOY / "railway.json").read_text(encoding="utf-8"))
    assert config["build"]["builder"] == "DOCKERFILE"
    assert (REPO / config["build"]["dockerfilePath"]).exists()
    deploy = config["deploy"]
    assert deploy["healthcheckPath"] == "/healthz" and deploy["requiredMountPath"] == "/data"
    assert deploy["numReplicas"] == 1 and deploy["restartPolicyType"] in ("ON_FAILURE", "ALWAYS")
    limits = deploy["limitOverride"]["containers"]
    assert limits["cpu"] <= 2 and limits["memoryBytes"] <= 4 * 2**30
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "SUPERCRONIC_SHA256=" in dockerfile and "sha256" in dockerfile
    for copied in re.findall(r"^COPY (\S+) ", dockerfile, re.M):
        assert (REPO / copied).exists(), copied


def test_server_requirements_cover_the_job_imports_and_skip_sklearn():
    text = (REPO / "app" / "requirements-server.txt").read_text(encoding="utf-8")
    names = {re.split(r"[=<>]", line)[0].strip().lower() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    assert {"numpy", "pandas", "scipy", "joblib", "requests", "boto3"} <= names
    assert not names & {"scikit-learn", "sklearn", "matplotlib", "fastapi", "uvicorn", "pytest"}
    assert "catboost" in (DEPLOY / "Dockerfile").read_text(encoding="utf-8")


def test_drop_page_cache_is_harmless(tmp_path):
    module = load(JOBS / "drop_page_cache.py", "drop_page_cache")
    (tmp_path / "big.bin").write_bytes(b"x" * (module.MIN_BYTES + 1))
    (tmp_path / "small.bin").write_bytes(b"x")
    seen, dropped = module.drop(str(tmp_path))
    if hasattr(os, "posix_fadvise"):
        assert (seen, dropped) == (1, 1)
    else:
        assert (seen, dropped) == (0, 0)
    assert (tmp_path / "big.bin").read_bytes() == b"x" * (module.MIN_BYTES + 1)


def test_stage_context_contains_only_what_the_image_needs(tmp_path):
    stage = load(DEPLOY / "stage_context.py", "stage_context")
    out = stage.stage(REPO, tmp_path / "ctx")
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert {"railway.json", ".dockerignore", "deploy/railway/Dockerfile", "app/requirements-server.txt",
            "deploy/railway/crontab", "deploy/railway/entrypoint.py", "app/scripts/run_scanner.py",
            "app/src/model_buk/paths.py"} <= files  # fmt: skip
    assert not any("__pycache__" in f or f.endswith(".pyc") or f.startswith("app/tests") or ".venv" in f for f in files)
    assert not any(f.startswith(("Audyt", "Football-Prediction", "reports/", "runtime/")) for f in files)
    for name in files:
        if name.endswith((".sh",)) or name == "deploy/railway/crontab":
            assert b"\r" not in (out / name).read_bytes()
    assert json.loads((out / "railway.json").read_text(encoding="utf-8"))["build"]["dockerfilePath"] == "deploy/railway/Dockerfile"
    shutil.rmtree(out)
