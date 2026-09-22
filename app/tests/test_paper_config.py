from datetime import timedelta
from pathlib import Path

import pytest

from model_buk.paper_config import (
    CONFIG_VERSION,
    PaperScanConfig,
    PaperScanConfigError,
    load_paper_scan_config,
)


CONFIG_PATH = Path(__file__).parents[1] / "config" / "paper_scan.toml"


def test_default_paper_scan_config_is_versioned_and_typed() -> None:
    config = load_paper_scan_config(CONFIG_PATH)

    assert config.version == CONFIG_VERSION
    assert isinstance(config.output_dir, Path)
    assert isinstance(config.history_path, Path)
    assert config.history_path == Path("data/multileague/europe16_matches_v05.csv.gz")
    assert config.api_day_offsets == (0, 1)
    assert config.max_fixtures == 10
    assert config.include_recent_observations is False
    assert config.quote_freshness == timedelta(minutes=30)
    assert config.required_feature_groups == {"fixture": 1, "odds": 1}
    assert config.max_recommendations == 10
    assert config.min_conservative_market_edge == 0.0
    assert config.devig_method == "proportional"
    assert "api_key" not in config.as_dict()
    assert "token" not in config.as_dict()


def test_config_id_is_stable_and_changes_with_configuration() -> None:
    first = PaperScanConfig()
    same = PaperScanConfig()
    changed = PaperScanConfig(execution_cost=0.01)

    assert first.config_id == same.config_id
    assert first.config_id != changed.config_id


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("api_day_offsets", (0, 0)),
        ("api_day_offsets", (-1,)),
        ("max_fixtures", 0),
        ("max_recommendations", 11),
        ("min_conservative_market_edge", -0.01),
        ("devig_method", "unknown"),
        ("tax_multiplier", 0.0),
        ("probability_haircut", 1.0),
        ("min_data_quality", 101.0),
        ("quote_freshness", timedelta(0)),
        ("required_feature_groups", {}),
    ],
)
def test_ranges_are_validated(field_name: str, value: object) -> None:
    with pytest.raises(PaperScanConfigError):
        PaperScanConfig(**{field_name: value})


def test_loader_supports_root_table_and_duration_aliases(tmp_path: Path) -> None:
    path = tmp_path / "scan.toml"
    path.write_text(
        """
version = "paper-scan-test-v1"
output_dir = "runs"
history_path = "history.csv"
engine_id = "engine-v2"
day_offsets = [0]
league_codes = ["PL"]
max_fixtures = 4
include_context = false
include_recent_observations = true
max_recommendations = 3
min_conservative_ev_net = 0.05
min_conservative_market_edge = 0.03
devig_method = "power"
tax_multiplier = 0.9
execution_cost = 0.01
probability_haircut = 0.1
min_data_quality = 75
quote_freshness_seconds = 90
required_feature_groups = { fixture = 1, odds = 2 }
""",
        encoding="utf-8",
    )

    config = PaperScanConfig.from_toml(path)
    assert config.engine_id == "engine-v2"
    assert config.day_offsets == (0,)
    assert config.include_recent_observations is True
    assert config.min_conservative_market_edge == 0.03
    assert config.devig_method == "power"
    assert config.quote_freshness == timedelta(seconds=90)
    assert config.required_feature_groups == {"fixture": 1, "odds": 2}
