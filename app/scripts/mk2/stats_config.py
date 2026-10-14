"""Competitions of the MK2 statistics model and of the dashboard.

TOP15: the top flights of the best associations in the UEFA country ranking 2026 (season-end
2025/26, kassiesa.net/uefa): England, Italy, Spain, Germany, France, Portugal, Netherlands,
Belgium, Türkiye, Czechia, Greece, Poland, Denmark, Norway and Switzerland (16th; Cyprus, 15th,
was left out at the user's request on 2026-10-14 -- the API has few statistics for it).  CUPS: Champions League,
Europa League, Conference League.  EXTRA: leagues the model also learns from (their clubs meet
ours in the cups, which is how the model compares leagues); shown in the dashboard on request.
Ids are API-Football league ids.
"""

from __future__ import annotations

TOP15: dict[int, str] = {
    39: "Premier League (Anglia)",
    135: "Serie A (Włochy)",
    140: "LaLiga (Hiszpania)",
    78: "Bundesliga (Niemcy)",
    61: "Ligue 1 (Francja)",
    94: "Liga Portugal (Portugalia)",
    88: "Eredivisie (Holandia)",
    144: "Pro League (Belgia)",
    203: "Süper Lig (Turcja)",
    345: "1. liga (Czechy)",
    197: "Super League (Grecja)",
    106: "Ekstraklasa (Polska)",
    119: "Superliga (Dania)",
    103: "Eliteserien (Norwegia)",
    207: "Super League (Szwajcaria)",
}
CUPS: dict[int, str] = {2: "Liga Mistrzów", 3: "Liga Europy", 848: "Liga Konferencji"}
EXTRA: dict[int, str] = {
    218: "Bundesliga (Austria)",
    179: "Premiership (Szkocja)",
    113: "Allsvenskan (Szwecja)",
    210: "HNL (Chorwacja)",
    286: "SuperLiga (Serbia)",
    333: "Premier Liha (Ukraina)",
    332: "Niké liga (Słowacja)",
}
MODEL_LEAGUES: dict[int, str] = {**TOP15, **CUPS, **EXTRA}
SHOWN: dict[int, str] = {**CUPS, **TOP15}

# Short labels for tight places in the dashboard.
SHORT: dict[int, str] = {
    2: "LM", 3: "LE", 848: "LK", 39: "ANG", 135: "WŁO", 140: "HIS", 78: "NIE", 61: "FRA", 94: "POR",
    88: "HOL", 144: "BEL", 203: "TUR", 345: "CZE", 197: "GRE", 106: "POL", 119: "DAN", 103: "NOR",
    207: "SZW", 218: "AUT", 179: "SZK", 113: "SZE", 210: "CHO", 286: "SRB", 333: "UKR", 332: "SŁO",
}
