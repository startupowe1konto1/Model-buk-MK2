"""Where the project's data lives (``Model-Buk-Data``).

Every job reads and writes under one data root.  It is taken from the ``MODEL_BUK_DATA`` environment variable
when that is set (the Railway container sets ``MODEL_BUK_DATA=/data``, the mount path of its volume); otherwise it
is the folder on the Windows PC, ``C:\\Users\\oskar\\Documents\\Model-Buk-Data``, exactly as before.  On a
non-Windows machine without the variable the root is ``/data`` (the container layout), never a Windows path.

The helpers below only build paths; they create nothing.  The layout is the one the collectors, the scanner and
the model jobs already use::

    <root>/api-football/                raw history archive (raw/, state.sqlite3) and tables/history.sqlite
    <root>/api-football-odds/           odds.sqlite3 (+ raw/), odds_snapshots.sqlite3 (compact, kept forever)
    <root>/api-football-prematch/       prematch.sqlite3 (+ raw/)
    <root>/api-football-extras/         tables/extras.sqlite (+ raw/, state.sqlite3)
    <root>/fixture-directory/           directory.sqlite3 (+ raw/)
    <root>/predictions/                 model_comparison.sqlite (pool weights) and the research forecasts
    <root>/scanner/                     scanner.sqlite (paper register), latest.html, html/, model_cache/
    <root>/logs/                        one log file per job
    <root>/archive/                     compressed exports waiting for the off-box copy (bucket / PC)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_VAR = "MODEL_BUK_DATA"
"""Environment variable that overrides the data root."""

WINDOWS_DEFAULT = Path(r"C:\Users\oskar\Documents\Model-Buk-Data")
"""The data root on the Windows PC (used when ``MODEL_BUK_DATA`` is not set)."""

POSIX_DEFAULT = Path("/data")
"""The data root on Linux without ``MODEL_BUK_DATA`` (the Railway volume's mount path)."""


def data_root() -> Path:
    """The data root: ``$MODEL_BUK_DATA`` if set (and not blank), else the platform default.

    Read on every call, so a test or a script can point it elsewhere by setting the variable before it asks.
    Module-level constants that were computed at import time (``DATA_ROOT = data_root()``) keep the value of
    the moment their module was imported.
    """
    value = os.environ.get(ENV_VAR, "").strip()
    if value:
        return Path(value).expanduser()
    return WINDOWS_DEFAULT if sys.platform == "win32" else POSIX_DEFAULT


def data_path(*parts: str) -> Path:
    """A path under the data root: ``data_path("scanner", "scanner.sqlite")``."""
    return data_root().joinpath(*parts)


def history_dir() -> Path:
    """Raw API-Football history archive (``raw/``, ``state.sqlite3``) and its ``tables/``."""
    return data_path("api-football")


def history_db() -> Path:
    return data_path("api-football", "tables", "history.sqlite")


def odds_dir() -> Path:
    return data_path("api-football-odds")


def odds_db() -> Path:
    return data_path("api-football-odds", "odds.sqlite3")


def odds_snapshots_db() -> Path:
    """The compact opening / pre-kickoff / closing snapshots kept after the full change history is archived."""
    return data_path("api-football-odds", "odds_snapshots.sqlite3")


def prematch_dir() -> Path:
    return data_path("api-football-prematch")


def prematch_db() -> Path:
    return data_path("api-football-prematch", "prematch.sqlite3")


def extras_dir() -> Path:
    return data_path("api-football-extras")


def directory_dir() -> Path:
    """The fixture directory (calendar, quota state, finished-match statistics)."""
    return data_path("fixture-directory")


def predictions_dir() -> Path:
    return data_path("predictions")


def scanner_dir() -> Path:
    return data_path("scanner")


def scanner_register() -> Path:
    return data_path("scanner", "scanner.sqlite")


def model_cache_dir() -> Path:
    return data_path("scanner", "model_cache")


def logs_dir() -> Path:
    return data_path("logs")


def archive_dir() -> Path:
    """Compressed exports (odds change history, register legs, backups) waiting for the off-box copy."""
    return data_path("archive")


__all__ = [
    "ENV_VAR",
    "POSIX_DEFAULT",
    "WINDOWS_DEFAULT",
    "archive_dir",
    "data_path",
    "data_root",
    "directory_dir",
    "extras_dir",
    "history_db",
    "history_dir",
    "logs_dir",
    "model_cache_dir",
    "odds_db",
    "odds_dir",
    "odds_snapshots_db",
    "predictions_dir",
    "prematch_db",
    "prematch_dir",
    "scanner_dir",
    "scanner_register",
]
