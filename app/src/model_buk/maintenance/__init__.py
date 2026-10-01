"""Housekeeping for the always-on server: keep the live databases small, archive what leaves them.

- :mod:`model_buk.maintenance.exports` -- the archive format: one export = a directory of gzip JSONL tables and a
  ``manifest.json`` with row counts and checksums, written atomically (``<id>.partial`` -> ``<id>``).
- :mod:`model_buk.maintenance.odds_retention` -- moves the full change history of fixtures that kicked off more
  than ``keep_days`` ago out of ``odds.sqlite3`` (keeping compact opening / pre-kickoff / closing snapshots).
- :mod:`model_buk.maintenance.register_retention` -- moves old near-miss leg rows out of the scanner register
  (never value legs, coupon legs, settlements, CLV or coupons) and keeps a weekly backup of the register.
- :mod:`model_buk.maintenance.bucket_sync` -- uploads the archive to an S3 bucket and pulls it back to the PC,
  merging exports into the PC's full databases.
"""
