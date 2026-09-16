from datetime import date, timedelta

import pandas as pd
import pytest


@pytest.fixture
def multileague_history_path(tmp_path):
    teams = ["Arsenal", "Chelsea", "Liverpool", "Everton"]
    rows = []
    start = date(2023, 8, 1)
    for index in range(120):
        home = teams[index % len(teams)]
        away = teams[(index + 1 + index // len(teams)) % len(teams)]
        if home == away:
            away = teams[(teams.index(home) + 1) % len(teams)]
        rows.append(
            {
                "Division": "E0",
                "MatchDate": start + timedelta(days=index * 3),
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHome": 1 + index % 3,
                "FTAway": index % 2,
                "HomeShots": 11 + index % 7,
                "AwayShots": 8 + index % 6,
                "HomeTarget": 4 + index % 4,
                "AwayTarget": 2 + index % 4,
                "HomeCorners": 4 + index % 5,
                "AwayCorners": 3 + index % 4,
                "HomeYellow": 1 + index % 3,
                "AwayYellow": 1 + (index + 1) % 3,
                "HomeRed": 0,
                "AwayRed": 0,
                "HomeElo": 1500 + (index % 4) * 20,
                "AwayElo": 1480 + (index % 4) * 20,
            }
        )
    path = tmp_path / "multileague.csv.gz"
    pd.DataFrame(rows).to_csv(path, index=False, compression="gzip")
    return path
