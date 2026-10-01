"""Ask the kernel to drop the cached pages of the data files (posix_fadvise DONTNEED), after every job.

A container's memory counter includes the page cache of the files it has read; the jobs read the odds archive
(~1 GB), the history tables and the raw history several times an hour, and that cache would otherwise stay
resident -- and billed -- between runs.  DONTNEED drops only clean pages (dirty pages stay until written back)
and never changes file contents, so it is safe while another job is using the same files.  Files smaller than
``MIN_BYTES`` are skipped (not worth a system call), and so are the ``raw/`` folders (12 000 small files of the
history archive that only the nightly history build reads; it passes ``--include-raw``).  No-op where
``os.posix_fadvise`` does not exist.

Usage: python3 drop_page_cache.py [DATA_ROOT] [--include-raw]      (default: $MODEL_BUK_DATA or /data)
"""

from __future__ import annotations

import os
import sys

MIN_BYTES = 64 * 1024


def drop(root: str, include_raw: bool = False) -> tuple[int, int]:
    if not hasattr(os, "posix_fadvise"):
        return 0, 0
    files = dropped = 0
    for directory, dirs, names in os.walk(root):
        if not include_raw:
            dirs[:] = [d for d in dirs if d != "raw"]
        for name in names:
            path = os.path.join(directory, name)
            try:
                if os.path.islink(path) or os.path.getsize(path) < MIN_BYTES:
                    continue
                fd = os.open(path, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                dropped += 1
            except OSError:
                pass
            finally:
                os.close(fd)
            files += 1
    return files, dropped


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--include-raw"]
    target = args[0] if args else os.environ.get("MODEL_BUK_DATA", "/data")
    seen, done = drop(target, include_raw="--include-raw" in sys.argv[1:])
    print(f"page cache dropped for {done} of {seen} files under {target}")
