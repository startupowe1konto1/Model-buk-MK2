#!/usr/bin/env bash
# Run one scheduled job the way the Windows .ps1 wrappers did: capture its output and append exactly one line
#   [YYYY-MM-DD HH:MM:SS] exit=N dur=Ss <output lines joined with " | ">
# to $MODEL_BUK_DATA/logs/<name>.log (rotated at LOG_MAX_BYTES, 3 old copies kept) and print the same line to
# stdout (Railway's log view).  Secrets never reach the log: the jobs print none.
#
# Usage: run_job.sh <name> <timeout-seconds> [--shared LOCK]... [--exclusive LOCK]... -- <command> [args...]
#
# Cost guards (Railway bills CPU and RAM per minute; the workspace has a hard limit):
# * A job never overlaps itself (non-blocking lock $DATA/locks/job-<name>.lock; a second start exits 75).
# * timeout(1) stops a runaway run (SIGTERM, then SIGKILL 60 s later).
# * Daily budget: the wall seconds of every finished run are added to $DATA/logs/usage-<YYYY-MM-DD>.tsv (Polish
#   date); once today's total exceeds DAILY_JOB_BUDGET_SECONDS (default 14400 = 4 h, about 3x the normal day)
#   further runs are skipped (exit 75) until midnight, so even a job that hangs on every run cannot run the bill
#   past a few dollars a month.  Set DAILY_JOB_BUDGET_SECONDS=0 to switch the guard off.
# Resource locks: --shared / --exclusive take named locks ($DATA/locks/<LOCK>.lock, flock(1)) waiting at most
# LOCK_WAIT_SECONDS (default 1800): the collectors and the scanner share "odds", the daily odds retention takes it
# exclusively, so it never deletes rows under a running scan.  Afterwards drop_page_cache.py tells the kernel to
# forget the cached pages of the data files, so file cache does not linger as billed memory between runs.
set -u

name="${1:?job name}"
timeout_s="${2:?timeout seconds}"
shift 2
locks=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    case "$1" in
        --shared) locks+=("s:${2:?lock name}"); shift 2 ;;
        --exclusive) locks+=("x:${2:?lock name}"); shift 2 ;;
        *) echo "run_job.sh: unknown option $1" >&2; exit 64 ;;
    esac
done
[ "${1:-}" = "--" ] || { echo "run_job.sh: missing -- before the command" >&2; exit 64; }
shift

DATA="${MODEL_BUK_DATA:-/data}"
APP_DIR="${APP_DIR:-/opt/model-buk/app}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$DATA/logs"
LOCK_DIR="$DATA/locks"
mkdir -p "$LOG_DIR" "$LOCK_DIR"
log="$LOG_DIR/$name.log"
usage="$LOG_DIR/usage-$(date '+%Y-%m-%d').tsv"
budget="${DAILY_JOB_BUDGET_SECONDS:-14400}"

max_bytes="${LOG_MAX_BYTES:-2000000}"
if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt "$max_bytes" ]; then
    rm -f "$log.3"
    [ -f "$log.2" ] && mv -f "$log.2" "$log.3"
    [ -f "$log.1" ] && mv -f "$log.1" "$log.2"
    mv -f "$log" "$log.1"
fi
find "$LOG_DIR" -maxdepth 1 -name 'usage-*.tsv' -mtime +40 -delete 2>/dev/null || true

out="$(mktemp)"
trap 'rm -f "$out"' EXIT
started=$(date +%s)
spent=0
[ -f "$usage" ] && spent=$(awk -F'\t' '{s += $3} END {print s + 0}' "$usage")
if [ "$budget" -gt 0 ] && [ "$spent" -ge "$budget" ]; then
    echo "skipped: daily job budget used (${spent}s of ${budget}s today; DAILY_JOB_BUDGET_SECONDS)" > "$out"
    code=75
else
    (
        flock -n 9 || { echo "skipped: the previous run of $name is still active"; exit 75; }
        for spec in "${locks[@]+"${locks[@]}"}"; do
            mode="${spec%%:*}"
            lock="${spec#*:}"
            exec {fd}>"$LOCK_DIR/$lock.lock"
            flock "-$mode" -w "${LOCK_WAIT_SECONDS:-1800}" "$fd" || { echo "skipped: lock $lock busy for ${LOCK_WAIT_SECONDS:-1800}s"; exit 75; }
        done
        cd "$APP_DIR" || exit 70
        timeout --signal=TERM --kill-after=60 "$timeout_s" "$@"
        code=$?
        [ "$code" -eq 124 ] && echo "killed: timeout after ${timeout_s}s"
        exit "$code"
    ) 9>"$LOCK_DIR/job-$name.lock" >"$out" 2>&1
    code=$?
fi
duration=$(( $(date +%s) - started ))
printf '%s\t%s\t%s\t%s\n' "$(date '+%H:%M:%S')" "$name" "$duration" "$code" >> "$usage"

summary="$(tail -n 40 "$out" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' | paste -sd $'\x1f' - | sed $'s/\x1f/ | /g' | cut -c1-4000)"
line="[$(date '+%Y-%m-%d %H:%M:%S')] exit=$code dur=${duration}s $summary"
printf '%s\n' "$line" >> "$log"
printf '%s %s\n' "$name" "$line"

if [ "$code" -ne 75 ]; then
    python3 "$HERE/drop_page_cache.py" "$DATA" ${DROP_CACHE_ARGS:-} >/dev/null 2>&1 || true
fi
exit "$code"
