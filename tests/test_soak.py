from pathlib import Path

from scrapyrealestate.runtime import RuntimePaths
from scrapyrealestate.soak import run_soak


def test_fixture_soak_has_independent_schedules_and_no_duplicates(tmp_path: Path):
    report = run_soak(RuntimePaths((tmp_path / "data").resolve()), cycles=6)

    assert report["search_count"] == 3
    assert tuple(report["scheduled_runs"].values()) == (6, 3, 2)
    assert report["same_search_conflicts"] == 1
    assert set(report["max_overlap_per_search"].values()) == {1}
    assert report["database_integrity"] == "ok"
    assert report["notification_event_count"] == 3
    assert report["succeeded_delivery_count"] == 3
    assert report["duplicate_delivery_pairs"] == 0
    assert report["active_fixture_attempts"] == 0
    assert (tmp_path / "data" / "soak-report.json").is_file()
