import inspect

import main
from scrapyrealestate.persistence.legacy_import import LegacyConfigImporter
from scrapyrealestate.persistence.legacy_seen import LegacySeenRepository


def test_process_entrypoint_contains_no_retired_runtime_paths():
    source = inspect.getsource(main)

    for retired_name in (
        "init_app_flask",
        "get_config_flask",
        "scrap_realestate",
        "check_new_flats",
        "run_spider",
        "telebot",
        "time.sleep",
        "subprocess",
        "\\n][",
    ):
        assert retired_name not in source


def test_legacy_migration_readers_remain_available_after_runtime_cleanup():
    assert LegacyConfigImporter.import_file
    assert LegacySeenRepository.import_file
