from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


ALIASES: dict[str, str] = {
    # England
    "manchester city": "Man City",
    "man city": "Man City",
    "manchester united": "Man United",
    "man united": "Man United",
    "nottingham forest": "Nottm Forest",
    "nott'm forest": "Nottm Forest",
    "afc bournemouth": "Bournemouth",
    "brighton & hove albion": "Brighton",
    "wolverhampton wanderers": "Wolves",
    # Spain
    "atletico madrid": "Ath Madrid",
    "atl. madrid": "Ath Madrid",
    "athletic club": "Ath Bilbao",
    "athletic bilbao": "Ath Bilbao",
    "real betis": "Betis",
    "real sociedad": "Sociedad",
    "rayo vallecano": "Vallecano",
    # Germany
    "bayern munich": "Bayern Munich",
    "borussia dortmund": "Dortmund",
    "borussia monchengladbach": "MGladbach",
    "borussia m'gladbach": "MGladbach",
    "eintracht frankfurt": "Ein Frankfurt",
    "bayer leverkusen": "Leverkusen",
    "1. fc koln": "FC Koln",
    "rb leipzig": "RB Leipzig",
    # Italy
    "ac milan": "Milan",
    "as roma": "Roma",
    "inter milan": "Inter",
    "internazionale": "Inter",
    # France
    "paris saint germain": "Paris SG",
    "paris saint-germain": "Paris SG",
    "psg": "Paris SG",
    # Poland
    "lech poznan": "Lech Poznan",
    "legia warszawa": "Legia",
    "jagiellonia bialystok": "Jagiellonia",
    "pogon szczecin": "Pogon Szczecin",
    "gornik zabrze": "Gornik Zabrze",
    "rakow czestochowa": "Rakow",
    "lechia gdansk": "Lechia Gdansk",
    "gks katowice": "GKS Katowice",
    "widzew lodz": "Widzew Lodz",
    "korona kielce": "Korona Kielce",
    "piast gliwice": "Piast Gliwice",
    "radomiak radom": "Radomiak Radom",
    "motor lublin": "Motor Lublin",
    "wisla plock": "Wisla Plock",
}


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\b(fc|afc|cf|sc|ssc|ac|as|sv|vfb|1\.)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_team_name(name: str, candidates: list[str]) -> tuple[str | None, float]:
    raw = str(name).strip()
    alias = ALIASES.get(normalize_name(raw))
    if alias and alias in candidates:
        return alias, 1.0
    if raw in candidates:
        return raw, 1.0

    target = normalize_name(raw)
    if not target:
        return None, 0.0
    best_name: str | None = None
    best_score = 0.0
    for candidate in candidates:
        score = SequenceMatcher(None, target, normalize_name(candidate)).ratio()
        if score > best_score:
            best_name, best_score = candidate, score
    if best_score >= 0.72:
        return best_name, best_score
    return None, best_score
