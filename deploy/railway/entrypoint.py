"""Container entry point on Railway: the job scheduler (supercronic) and the password-protected scanner page.

Runs under ``tini`` (PID 1, reaps zombies, forwards signals).  It

1. starts a small HTTP server on ``$PORT`` (stdlib only, one thread per request):

   - ``/healthz`` -- no authentication, always 200 while the process lives; the JSON body says whether the
     scheduler runs (``ok``), waits for the initial data (``waiting_for_seed``) or is down (``cron_down``);
   - ``/`` and ``/latest.html`` -- ``<data>/scanner/latest.html``; ``/html/`` (index) and ``/html/<day>.html``;
     ``/latest_value_legs.csv``, ``/latest_coupons.csv``, ``/csv/`` and ``/csv/<file>.csv``; ``/status`` -- disk
     usage, scheduler state and the last line of every job log.  All of them need HTTP Basic Auth with
     ``SCANNER_PAGE_USER`` (default ``model-buk``) / ``SCANNER_PAGE_PASSWORD``; while the password is not set
     every page answers 503 and nothing is served.

2. waits for the initial data: until ``<data>/.seeded`` exists it looks every 30 s for ``<data>/seed.tar.gz``
   with a matching ``<data>/seed.tar.gz.sha256`` (upload the checksum *after* the archive, so a half-uploaded
   archive is never opened), extracts it safely into ``<data>`` (``tarfile`` "data" filter), writes
   ``.seeded`` and deletes the archive.  ``ALLOW_EMPTY_DATA=1`` skips the wait (fresh start without data).

3. starts ``supercronic`` on the crontab as a child and restarts it if it dies (at most
   ``MAX_CRON_RESTARTS`` times per hour, then the container exits so that Railway restarts it).

``SIGTERM`` / ``SIGINT``: the HTTP server stops, supercronic gets ``SIGTERM`` (it lets running jobs finish) and
is waited for up to ``STOP_GRACE_SECONDS`` (default 25) before it is killed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
DEFAULT_USER = "model-buk"
SEED_NAME = "seed.tar.gz"
SEEDED_MARKER = ".seeded"
SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


@dataclass
class Settings:
    data_root: Path
    port: int
    user: str
    password: str | None
    crontab: Path
    supercronic: str
    allow_empty: bool
    max_cron_restarts: int
    stop_grace: float

    @classmethod
    def from_env(cls, env: dict[str, str] | os._Environ | None = None) -> Settings:
        source = os.environ if env is None else env
        password = (source.get("SCANNER_PAGE_PASSWORD") or "").strip() or None
        return cls(
            data_root=Path(source.get("MODEL_BUK_DATA") or "/data"),
            port=int(source.get("PORT") or 8080),
            user=(source.get("SCANNER_PAGE_USER") or "").strip() or DEFAULT_USER,
            password=password,
            crontab=Path(source.get("CRONTAB") or HERE / "crontab"),
            supercronic=source.get("SUPERCRONIC") or "supercronic",
            allow_empty=(source.get("ALLOW_EMPTY_DATA") or "").strip() in ("1", "true", "yes"),
            max_cron_restarts=int(source.get("MAX_CRON_RESTARTS") or 5),
            stop_grace=float(source.get("STOP_GRACE_SECONDS") or 25),
        )


# --------------------------------------------------------------------------- #
# Authentication (pure functions, tested)
# --------------------------------------------------------------------------- #


def check_basic_auth(header: str | None, user: str, password: str | None) -> bool:
    """True when ``header`` is ``Basic <base64(user:password)>`` for these credentials (constant-time compare).

    Always False while no password is configured: pages are never served without one.
    """
    if not password or not header:
        return False
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "basic" or not value:
        return False
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    given_user, sep, given_password = decoded.partition(":")
    if not sep:
        return False
    user_ok = hmac.compare_digest(given_user.encode("utf-8"), user.encode("utf-8"))
    password_ok = hmac.compare_digest(given_password.encode("utf-8"), password.encode("utf-8"))
    return user_ok and password_ok


def resolve_page(data_root: Path, url_path: str) -> Path | None:
    """The file a page URL maps to, or None (unknown URL, traversal attempt, or not a plain file name)."""
    path = unquote(urlsplit(url_path).path)
    scanner = data_root / "scanner"
    if path in ("/", "/latest.html", "/index.html"):
        return scanner / "latest.html"
    if path in ("/latest_value_legs.csv", "/latest_coupons.csv"):
        return scanner / path.lstrip("/")
    for folder, suffix in (("html", ".html"), ("csv", ".csv")):
        prefix = f"/{folder}/"
        if path.startswith(prefix):
            name = path[len(prefix) :]
            if not name or not name.endswith(suffix) or not set(name) <= SAFE_NAME_CHARS or name.startswith("."):
                return None
            return scanner / folder / name
    return None


# --------------------------------------------------------------------------- #
# Initial data (seed archive)
# --------------------------------------------------------------------------- #


def seed_ready(data_root: Path) -> bool:
    """A complete seed upload: the archive and its checksum file are both there and the checksum matches."""
    archive = data_root / SEED_NAME
    checksum = data_root / (SEED_NAME + ".sha256")
    if not archive.is_file() or not checksum.is_file():
        return False
    expected = checksum.read_text(encoding="utf-8").split()[0].strip().lower() if checksum.stat().st_size else ""
    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return bool(expected) and hmac.compare_digest(digest.hexdigest(), expected)


def extract_seed(data_root: Path, log=print) -> dict:
    """Extract ``seed.tar.gz`` into the data root (refusing absolute paths, ``..`` and links outside), then remove
    the archive and its checksum and write the ``.seeded`` marker.  Returns the marker's content."""
    archive = data_root / SEED_NAME
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        needed = sum(m.size for m in members if m.isfile())
        free = shutil.disk_usage(data_root).free
        if needed > free:
            raise RuntimeError(f"seed needs {needed >> 20} MiB, only {free >> 20} MiB free on the volume")
        log(f"seed: extracting {len(members)} entries ({needed >> 20} MiB) into {data_root}")
        tar.extractall(data_root, filter="data")
    marker = {"seeded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "entries": len(members), "bytes": needed}
    (data_root / SEEDED_MARKER).write_text(json.dumps(marker), encoding="utf-8")
    archive.unlink(missing_ok=True)
    (data_root / (SEED_NAME + ".sha256")).unlink(missing_ok=True)
    return marker


# --------------------------------------------------------------------------- #
# Scheduler supervision
# --------------------------------------------------------------------------- #


@dataclass
class State:
    settings: Settings
    phase: str = "starting"  # waiting_for_seed | seeding | running | stopping
    cron: subprocess.Popen | None = None
    cron_restarts: deque = field(default_factory=lambda: deque(maxlen=50))
    stopping: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.time)

    def cron_running(self) -> bool:
        return self.cron is not None and self.cron.poll() is None

    def health(self) -> dict:
        if self.phase in ("waiting_for_seed", "seeding"):
            status = self.phase
        elif self.cron_running():
            status = "ok"
        else:
            status = "cron_down" if self.phase == "running" else self.phase
        return {"status": status, "uptime_s": int(time.time() - self.started), "cron_restarts": len(self.cron_restarts)}


def start_cron(state: State) -> None:
    settings = state.settings
    state.cron = subprocess.Popen([settings.supercronic, "-passthrough-logs", "-quiet", "-no-reap", str(settings.crontab)])
    print(f"entrypoint: supercronic started (pid {state.cron.pid}) with {settings.crontab}", flush=True)


def start_initial_fit(state: State) -> None:
    """Right after the seed is extracted: refit the live models once (the seed's fit may be older than the
    scanner accepts; otherwise models would be skipped until the 03:00 job).  Same wrapper as the nightly job."""
    script = HERE / "jobs" / "fit_live_models.sh"
    if script.exists():
        subprocess.Popen(["bash", str(script)])
        print("entrypoint: initial fit_live_models started", flush=True)


def supervise(state: State) -> int:
    """Keep supercronic alive until stopping; returns the exit code for the container."""
    while not state.stopping.is_set():
        if state.cron is not None and state.cron.poll() is not None:
            code = state.cron.returncode
            now = time.time()
            recent = [t for t in state.cron_restarts if now - t < 3600]
            if len(recent) >= state.settings.max_cron_restarts:
                print(f"entrypoint: supercronic exited ({code}) {len(recent)} times within an hour; giving up", flush=True)
                return 1
            state.cron_restarts.append(now)
            print(f"entrypoint: supercronic exited with {code}; restarting", flush=True)
            time.sleep(5)
            start_cron(state)
        state.stopping.wait(5)
    return 0


def wait_for_seed(state: State) -> bool:
    """Block until the data root is seeded (or seeding is not required); False when stopping."""
    root = state.settings.data_root
    root.mkdir(parents=True, exist_ok=True)
    if state.settings.allow_empty or (root / SEEDED_MARKER).exists():
        return True
    state.phase = "waiting_for_seed"
    print(f"entrypoint: waiting for {root / SEED_NAME} and its .sha256 (or set ALLOW_EMPTY_DATA=1)", flush=True)
    checked: tuple | None = None  # (size, mtime) of the pair last hashed: a 0.75 GB file is hashed once, not every 30 s
    while not state.stopping.is_set():
        try:
            pair = _seed_signature(root)
            if pair is not None and pair != checked and seed_ready(root):
                state.phase = "seeding"
                marker = extract_seed(root, log=lambda text: print(f"entrypoint: {text}", flush=True))
                print(f"entrypoint: seed extracted {marker}", flush=True)
                return True
            if pair is not None and pair != checked:
                print("entrypoint: seed.tar.gz does not match seed.tar.gz.sha256 (yet); waiting for a complete upload", flush=True)
            checked = pair
        except Exception as error:  # noqa: BLE001 - keep waiting; a re-upload can fix it
            print(f"entrypoint: seed not usable yet: {type(error).__name__}: {error}", flush=True)
            state.phase = "waiting_for_seed"
            checked = _seed_signature(root)  # do not retry the same broken upload every 30 s
        state.stopping.wait(30)
    return False


def _seed_signature(root: Path) -> tuple | None:
    """``(size, mtime)`` of the archive and of its checksum file, or None while either is missing."""
    try:
        archive = (root / SEED_NAME).stat()
        checksum = (root / (SEED_NAME + ".sha256")).stat()
    except OSError:
        return None
    return (archive.st_size, archive.st_mtime_ns, checksum.st_size, checksum.st_mtime_ns)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def status_text(state: State) -> str:
    settings = state.settings
    lines = [f"health: {json.dumps(state.health())}"]
    try:
        usage = shutil.disk_usage(settings.data_root)
        lines.append(f"volume {settings.data_root}: used {usage.used / 2**30:.2f} GiB, free {usage.free / 2**30:.2f} GiB of {usage.total / 2**30:.2f} GiB")
    except OSError as error:
        lines.append(f"volume: {error}")
    logs = settings.data_root / "logs"
    lines.append("")
    for path in sorted(logs.glob("*.log")) if logs.exists() else []:
        try:
            with open(path, "rb") as handle:
                handle.seek(max(0, path.stat().st_size - 4096))
                tail = handle.read().decode("utf-8", "replace").strip().splitlines()
            lines.append(f"[{path.stem}] {tail[-1][:600] if tail else '(empty)'}")
        except OSError as error:
            lines.append(f"[{path.stem}] {error}")
    return "\n".join(lines) + "\n"


def make_handler(state: State):
    settings = state.settings

    class Handler(BaseHTTPRequestHandler):
        server_version = "model-buk"
        sys_version = ""

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - quiet: no per-request log lines
            return

        def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8", extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send(200, json.dumps(state.health()).encode("utf-8"), "application/json")
                return
            if not settings.password:
                self._send(503, b"The page is disabled: SCANNER_PAGE_PASSWORD is not set.\n")
                return
            if not check_basic_auth(self.headers.get("Authorization"), settings.user, settings.password):
                time.sleep(0.5)  # slows down guessing; one thread per request, the server stays responsive
                self._send(401, b"Authentication required.\n", extra={"WWW-Authenticate": 'Basic realm="Model Buk", charset="UTF-8"'})
                return
            if path == "/status":
                self._send(200, status_text(state).encode("utf-8"))
                return
            if path in ("/html/", "/csv/"):
                folder = settings.data_root / "scanner" / path.strip("/")
                names = sorted((p.name for p in folder.glob("*") if p.is_file()), reverse=True) if folder.exists() else []
                items = "".join(f'<li><a href="{html.escape(n)}">{html.escape(n)}</a></li>' for n in names)
                body = f"<!doctype html><meta charset=utf-8><title>Model Buk {path}</title><ul>{items}</ul>"
                self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")
                return
            target = resolve_page(settings.data_root, path)
            if target is None or not target.is_file():
                self._send(404, b"Not found.\n")
                return
            content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "text/csv; charset=utf-8"
            try:
                body = target.read_bytes()
            except OSError:
                self._send(503, b"The page is being rewritten; try again.\n")
                return
            self._send(200, body, content_type)

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, b"Method not allowed.\n", extra={"Allow": "GET, HEAD"})

        do_PUT = do_DELETE = do_PATCH = do_POST  # noqa: N815

    return Handler


def main() -> int:
    settings = Settings.from_env()
    state = State(settings)
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), make_handler(state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    print(
        f"entrypoint: serving on :{settings.port} (page {'enabled' if settings.password else 'DISABLED: no SCANNER_PAGE_PASSWORD'}), "
        f"data {settings.data_root}, TZ={os.environ.get('TZ', 'UTC')}",
        flush=True,
    )

    def stop(signum, _frame) -> None:
        print(f"entrypoint: signal {signum}, stopping", flush=True)
        state.phase = "stopping"
        state.stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    code = 0
    was_seeded = (settings.data_root / SEEDED_MARKER).exists()
    if wait_for_seed(state):
        state.phase = "running"
        start_cron(state)
        if not was_seeded and not settings.allow_empty:
            start_initial_fit(state)
        code = supervise(state)
    if state.cron is not None and state.cron.poll() is None:
        state.cron.send_signal(signal.SIGTERM)
        try:
            state.cron.wait(timeout=settings.stop_grace)
        except subprocess.TimeoutExpired:
            state.cron.kill()
    server.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
