"""Elo liczone wylacznie do przodu.

Budujemy wlasne zamiast brac gotowe HomeElo/AwayElo z club-football z dwoch
powodow. Po pierwsze pokrycie: cudze Elo jest przy 57 344 z naszych 86 025
meczow (66,7%) i konczy sie w czerwcu 2025, wiec traci caly sezon 25/26.
Po drugie audytowalnosc: przy wlasnym Elo widac w kodzie, ze rating pochodzi
wylacznie z meczow wczesniejszych. Przy cudzym trzeba w to uwierzyc, a
[[11 - Czas predykcji i leakage]] kaze takie zalozenia sprawdzac, nie zakladac.

KONTRAKT: wolajacy odczytuje diff()/expected_home() PRZED update() dla kazdego
meczu, idac chronologicznie. Ta klasa nie zna dat i nie moze tego wymusic.
"""
from typing import Dict

SCORE: Dict[str, float] = {"H": 1.0, "D": 0.5, "A": 0.0}


class EloTable:
    def __init__(self, k: float = 20.0, home_adv: float = 60.0, start: float = 1500.0):
        self.k = k
        self.home_adv = home_adv
        self.start = start
        self._ratings: Dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self._ratings.get(team, self.start)

    def diff(self, home: str, away: str) -> float:
        """Przewaga gospodarza w punktach Elo, z bonusem za gre u siebie."""
        return self.rating(home) + self.home_adv - self.rating(away)

    def expected_home(self, home: str, away: str) -> float:
        return 1.0 / (1.0 + 10.0 ** (-self.diff(home, away) / 400.0))

    def update(self, home: str, away: str, ftr: str) -> None:
        expected = self.expected_home(home, away)
        actual = SCORE[ftr]
        delta = self.k * (actual - expected)
        self._ratings[home] = self.rating(home) + delta
        self._ratings[away] = self.rating(away) - delta
