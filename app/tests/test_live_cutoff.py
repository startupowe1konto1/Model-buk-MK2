from model_buk.live_provider import ApiFootballClient


class FakeClient(ApiFootballClient):
    def __init__(self):
        super().__init__(api_key="test")

    def recent_fixtures(self, team_id: int, last: int = 5):
        return [
            {
                "fixture_id": 10,
                "kickoff": "2026-09-10T18:00:00+00:00",
                "status": "FT",
                "home": {"id": team_id, "name": "A", "goals": 1},
                "away": {"id": 2, "name": "B", "goals": 0},
            },
            {
                "fixture_id": 20,
                "kickoff": "2026-09-20T18:00:00+00:00",
                "status": "FT",
                "home": {"id": team_id, "name": "A", "goals": 2},
                "away": {"id": 3, "name": "C", "goals": 0},
            },
        ]

    def fixture_statistics(self, fixture_id: int):
        return {}


def test_current_observations_respect_prediction_cutoff():
    rows = FakeClient().current_observations(
        1,
        prediction_cutoff="2026-09-15T12:00:00+00:00",
        exclude_fixture_id=999,
    )
    assert [row["fixture_id"] for row in rows] == [10]
    assert rows[0]["known_at"]


def test_current_observations_exclude_target_fixture():
    rows = FakeClient().current_observations(
        1,
        prediction_cutoff="2026-09-30T12:00:00+00:00",
        exclude_fixture_id=10,
    )
    assert [row["fixture_id"] for row in rows] == [20]
