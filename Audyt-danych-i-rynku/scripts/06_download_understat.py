#!/usr/bin/env python3
"""Pobiera xG z Understat (top 5 lig) przez soccerdata. Zapisuje CSV do data/raw/understat/."""
import logging, warnings, os
logging.disable(logging.CRITICAL); warnings.filterwarnings("ignore")
import soccerdata as sd

LEAGUES = ["ENG-Premier League", "ESP-La Liga", "ITA-Serie A", "GER-Bundesliga", "FRA-Ligue 1"]
SEASONS = ["2014", "2015", "2016", "2017", "2018", "2019",
           "2020", "2021", "2022", "2023", "2024", "2025"]
OUT = "data/raw/understat"
os.makedirs(OUT, exist_ok=True)

for lg in LEAGUES:
    slug = lg.split("-")[0].lower()
    try:
        us = sd.Understat(leagues=lg, seasons=SEASONS)
        sch = us.read_schedule()
        sch.to_csv(f"{OUT}/{slug}_schedule.csv")
        print(f"{lg:22s} schedule {sch.shape}")
        try:
            ts = us.read_team_match_stats()
            ts.to_csv(f"{OUT}/{slug}_team_match_stats.csv")
            print(f"{'':22s} team_stats {ts.shape}")
        except Exception as e:
            print(f"{'':22s} team_stats BLAD: {str(e)[:70]}")
    except Exception as e:
        print(f"{lg:22s} BLAD: {str(e)[:80]}")
