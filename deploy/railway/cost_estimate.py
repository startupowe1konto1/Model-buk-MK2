"""Monthly Railway cost of the job set, from the measured runtimes (2026-10-01, on the PC) and the Hobby prices.

    python deploy/railway/cost_estimate.py              # table + total (Markdown)
    python deploy/railway/cost_estimate.py --slowdown 2  # assume the Railway vCPU is 2x slower than the PC

Model (deliberately pessimistic):

- CPU: measured CPU seconds per run x ``slowdown`` (default 1.5: a shared server vCPU is slower than the PC's
  desktop core), billed per vCPU-minute ($20 / vCPU-month).
- RAM: every run is billed at its peak RSS for every started minute of its wall time (rounded up), plus, in
  scenario B, the page cache of the database files the job reads (Railway counts container memory; the job
  wrapper drops that cache after every run, so it is only paid while the job runs).
- Between runs only the page server, supercronic and tini are resident (measured ~27 MB for the server; 45 MB
  assumed for all three).
- Volume $0.15 / GB-month on the space used, bucket $0.015 / GB-month, egress $0.05 / GB.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

MINUTES_PER_MONTH = 30 * 24 * 60
RAM_PER_GB_MINUTE = 10.0 / MINUTES_PER_MONTH
CPU_PER_VCPU_MINUTE = 20.0 / MINUTES_PER_MONTH
VOLUME_PER_GB_MONTH = 0.15
BUCKET_PER_GB_MONTH = 0.015
EGRESS_PER_GB = 0.05


@dataclass(frozen=True)
class Job:
    name: str
    runs_per_month: float
    cpu_s: float  # measured CPU seconds per run on the PC
    wall_extra_s: float  # wall time beyond CPU (network waits, sleeps between API requests)
    rss_gb: float  # peak RSS (Linux estimate = Windows peak private bytes, rounded up)
    cache_gb: float  # data files read during the run (page cache, scenario B only)
    note: str


JOBS: tuple[Job, ...] = (
    Job("odds_collector (hourly, 20 normal)", 600, 7.0, 6.0, 0.15, 0.35, "measured 1.1 s for 16 fixtures; ~160 fixtures/run"),
    Job("odds_collector (wide, every 6 h)", 120, 21.0, 10.0, 0.20, 0.35, "~500 fixtures, 64 requests"),
    Job("prematch_collector (3/h)", 2160, 1.0, 3.0, 0.08, 0.0, "measured 0.7 s, 59 MB"),
    Job("closing_odds (4/h)", 2880, 1.5, 4.0, 0.10, 0.35, "measured 0.6 s with nothing due"),
    Job("scanner (2/h)", 1440, 43.3, 1.0, 1.25, 0.60, "measured 28-43 s, 1.20 GB (replay of 30.09 19:45, 450 fixtures)"),
    Job("refresh_recent (hourly)", 720, 1.0, 3.0, 0.08, 0.0, "measured 0.7 s"),
    Job("refresh_season (daily)", 30, 5.0, 60.0, 0.10, 0.0, "~90 requests with 0.25 s sleeps"),
    Job("history_build (daily, slim)", 30, 120.0, 5.0, 0.55, 0.55, "measured 116 s, 531 MB private"),
    Job("fit_live_models (daily, 1 worker)", 30, 23.0, 1.0, 1.00, 0.40, "measured 21 s (75 s on a booster day, 1 in 28)"),
    Job("compact_odds (daily)", 30, 10.0, 2.0, 0.30, 0.35, "measured 10 s for 228 fixtures incl. VACUUM"),
    Job("compact_register (daily)", 30, 1.0, 1.0, 0.05, 0.02, "measured 0.6 s"),
    Job("archive_upload (daily)", 30, 3.0, 10.0, 0.08, 0.0, "~10 MB/day to the bucket"),
    Job("drop_page_cache (after every run)", 8100, 0.15, 0.0, 0.02, 0.0, "python start + fadvise walk"),
)

IDLE_GB = 0.045
VOLUME_USED_GB = 3.6
BUCKET_GB_AFTER_A_YEAR = 2.5
EGRESS_GB = 0.5


def job_cost(job: Job, slowdown: float, with_cache: bool) -> tuple[float, float, float, float]:
    """``(cpu minutes, GB-minutes, cpu $, ram $)`` per month."""
    cpu_min = job.runs_per_month * job.cpu_s * slowdown / 60.0
    wall_min = math.ceil((job.cpu_s * slowdown + job.wall_extra_s) / 60.0)
    memory = job.rss_gb + (job.cache_gb if with_cache else 0.0)
    gb_min = job.runs_per_month * wall_min * memory
    return cpu_min, gb_min, cpu_min * CPU_PER_VCPU_MINUTE, gb_min * RAM_PER_GB_MINUTE


def table(slowdown: float) -> str:
    lines = [
        f"| job | runs/month | CPU min | RAM GB-min (A / B) | $ CPU | $ RAM A | $ RAM B | note |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    total_cpu = total_a = total_b = 0.0
    for job in JOBS:
        cpu_min, gb_a, cpu_usd, ram_a = job_cost(job, slowdown, False)
        _, gb_b, _, ram_b = job_cost(job, slowdown, True)
        total_cpu += cpu_usd
        total_a += ram_a
        total_b += ram_b
        lines.append(
            f"| {job.name} | {job.runs_per_month:.0f} | {cpu_min:.0f} | {gb_a:.0f} / {gb_b:.0f} | {cpu_usd:.2f} | {ram_a:.2f} | {ram_b:.2f} | {job.note} |"
        )
    idle = IDLE_GB * MINUTES_PER_MONTH * RAM_PER_GB_MINUTE
    volume = VOLUME_USED_GB * VOLUME_PER_GB_MONTH
    bucket = BUCKET_GB_AFTER_A_YEAR * BUCKET_PER_GB_MONTH
    egress = EGRESS_GB * EGRESS_PER_GB
    fixed = idle + volume + bucket + egress
    lines += [
        f"| idle (page server + supercronic) | always | 0 | {IDLE_GB * MINUTES_PER_MONTH:.0f} | 0.00 | {idle:.2f} | {idle:.2f} | {IDLE_GB * 1000:.0f} MB resident |",
        f"| volume ({VOLUME_USED_GB} GB used) | | | | | {volume:.2f} | {volume:.2f} | $0.15/GB-month |",
        f"| bucket (~{BUCKET_GB_AFTER_A_YEAR} GB after a year) | | | | | {bucket:.2f} | {bucket:.2f} | $0.015/GB-month |",
        f"| egress (~{EGRESS_GB} GB) | | | | | {egress:.2f} | {egress:.2f} | page views, uploads |",
        f"| **total** | | | | **{total_cpu:.2f}** | | | **A: ${total_cpu + total_a + fixed:.2f}  B: ${total_cpu + total_b + fixed:.2f} per month** |",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slowdown", type=float, default=1.5, help="Railway CPU time / PC CPU time")
    args = parser.parse_args()
    print(table(args.slowdown))


if __name__ == "__main__":
    main()
