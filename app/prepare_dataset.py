from __future__ import annotations

import argparse
from pathlib import Path

from model_buk.data import load_match_frame
from model_buk.features import build_features
from model_buk.constants import CORE_FEATURES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", required=True)
    parser.add_argument("--understat", required=True)
    parser.add_argument("--footiqo-corners", required=True)
    parser.add_argument("--big-five", required=True)
    parser.add_argument("--out", default="data/processed/epl_total_corners_v01.csv.gz")
    parser.add_argument("--raw-out", default="data/canonical/epl_matches_v01.csv.gz")
    args = parser.parse_args()

    raw = load_match_frame(args.matches, args.understat, args.footiqo_corners, args.big_five)

    raw_out = Path(args.raw_out)
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(raw_out, index=False, compression="gzip" if raw_out.suffix == ".gz" else None)

    features = build_features(raw)
    odds_cols = [c for c in features.columns if "corners_ft_closing_odds" in c]
    cols = [
        "date", "home_team", "away_team", "source", "home_corners", "away_corners", "total_corners",
        *CORE_FEATURES, *odds_cols,
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    features[cols].to_csv(out, index=False, compression="gzip" if out.suffix == ".gz" else None)
    print(f"saved canonical {len(raw):,} rows -> {raw_out}")
    print(f"saved features  {len(features):,} rows -> {out}")


if __name__ == "__main__":
    main()
