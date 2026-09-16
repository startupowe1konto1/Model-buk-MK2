from __future__ import annotations

from typing import Any

import numpy as np


FIELD_MAP = {
    "goals": ("goals_for", "goals_against"),
    "shots": ("shots_for", "shots_against"),
    "sot": ("sot_for", "sot_against"),
    "corners": ("corners_for", "corners_against"),
    "cards": ("cards_for", "cards_against"),
}


def _values(rows: list[dict[str, Any]], field: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            out.append(value)
    return out


def _shrunk_recent(values: list[float], prior: float, prior_games: float = 6.0) -> tuple[float, int]:
    if not values:
        return float(prior), 0
    return float((sum(values) + prior * prior_games) / (len(values) + prior_games)), len(values)


def apply_current_observations(
    analysis: dict[str, Any],
    home_rows: list[dict[str, Any]],
    away_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Blend on-demand current-season observations into the historical baseline.

    The adjustment is deterministic and shrunk toward the historical point-in-time
    rate. It is intentionally capped; live inputs can update a stale baseline but
    cannot overwhelm it on a handful of matches.
    """
    adjusted = {k: (v.copy() if isinstance(v, dict) else v) for k, v in analysis.items()}
    expected = {k: v.copy() for k, v in analysis["expected"].items()}
    live_factors: list[dict[str, Any]] = []
    samples: dict[str, dict[str, int]] = {}

    historical_factor_map = {
        f.get("key"): f for f in analysis.get("factors", []) if isinstance(f, dict)
    }

    for metric, (for_field, against_field) in FIELD_MAP.items():
        base = expected[metric]
        hist_factor = historical_factor_map.get(f"{metric}_rates", {})
        hist_home = hist_factor.get("home", {})
        hist_away = hist_factor.get("away", {})
        h_for_prior = float(hist_home.get("for") or base["home"])
        h_against_prior = float(hist_home.get("against") or base["away"])
        a_for_prior = float(hist_away.get("for") or base["away"])
        a_against_prior = float(hist_away.get("against") or base["home"])

        h_for, hfn = _shrunk_recent(_values(home_rows, for_field), h_for_prior)
        h_against, han = _shrunk_recent(_values(home_rows, against_field), h_against_prior)
        a_for, afn = _shrunk_recent(_values(away_rows, for_field), a_for_prior)
        a_against, aan = _shrunk_recent(_values(away_rows, against_field), a_against_prior)

        home_factor = np.sqrt(max(h_for / max(h_for_prior, 0.05), 0.05) * max(a_against / max(a_against_prior, 0.05), 0.05))
        away_factor = np.sqrt(max(a_for / max(a_for_prior, 0.05), 0.05) * max(h_against / max(h_against_prior, 0.05), 0.05))
        home_factor = float(np.clip(home_factor, 0.82, 1.18))
        away_factor = float(np.clip(away_factor, 0.82, 1.18))

        if max(hfn, han, afn, aan) > 0:
            base["home"] = round(float(base["home"] * home_factor), 3)
            base["away"] = round(float(base["away"] * away_factor), 3)
            base["total"] = round(float(base["home"] + base["away"]), 3)
            base["live_adjustment_home"] = round(home_factor, 4)
            base["live_adjustment_away"] = round(away_factor, 4)
            base["source"] = "historical_baseline_plus_current_api"

            live_factors.append({
                "key": f"live_{metric}",
                "label": f"Bieżące {metric}: ostatnie mecze",
                "home": {"for": round(h_for, 2), "against": round(h_against, 2), "n": max(hfn, han)},
                "away": {"for": round(a_for, 2), "against": round(a_against, 2), "n": max(afn, aan)},
                "status": "model_input",
                "explanation": "Aktualne statystyki z API są shrinkowane do historycznego baseline i wpływają na oczekiwane count-y.",
            })
        samples[metric] = {"home": max(hfn, han), "away": max(afn, aan)}

    adjusted["expected"] = expected
    adjusted["factors"] = analysis.get("factors", []) + live_factors
    adjusted.setdefault("current_data", {})["observation_samples"] = samples
    adjusted["current_data"]["used_in_model"] = bool(live_factors)
    return adjusted
