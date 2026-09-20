import json
import os
import threading
import time

import pytest

from controller.settings import SCHEMA, Settings, prepare_settings_file


@pytest.fixture
def store(tmp_path):
    """A Settings backed by a throwaway file, with the debounce timer off so
    tests can assert on saves deterministically."""
    return Settings(path=str(tmp_path / "settings.json"), autosave=False)


def test_defaults_match_the_schema(store):
    for setting in SCHEMA:
        assert store.get(setting.key) == setting.default


def test_values_are_clamped_not_rejected(store):
    applied, rejected = store.update({"display.brightness": 9999})
    assert applied == {"display.brightness": 100}
    assert rejected == []

    store.update({"display.brightness": -50})
    assert store.get("display.brightness") == 1


def test_unusable_values_are_rejected_without_disturbing_the_rest(store):
    """One bad field in a request mustn't stop the others applying, and must
    never leave a value a render thread can't use."""
    applied, rejected = store.update(
        {
            "display.brightness": 40,
            "display.speed": "not a number",
            "clock.units": "kelvin",
            "snake.body_color": [1, 2],
            "nonexistent.key": 1,
        }
    )
    assert applied == {"display.brightness": 40}
    assert sorted(rejected) == [
        "clock.units",
        "display.speed",
        "nonexistent.key",
        "snake.body_color",
    ]
    assert store.get("display.speed") == 1.0
    assert store.get("clock.units") == "fahrenheit"


def test_types_are_coerced(store):
    store.update(
        {
            "display.web_fps": "24",  # int from a form field
            "clock.show_weather": "false",  # bool from a checkbox
            "snake.head_color": [300, -5, 7.9],
        }
    )
    assert store.get("display.web_fps") == 24
    assert store.get("clock.show_weather") is False
    assert store.get("snake.head_color") == (255, 0, 7)


def test_save_and_reload_round_trips(store):
    store.update({"display.brightness": 33, "snake.apple_color": [1, 2, 3]})
    assert store.save() is True

    reloaded = Settings(path=store.path, autosave=False)
    assert reloaded.get("display.brightness") == 33
    assert reloaded.get("snake.apple_color") == (1, 2, 3)


def test_saved_file_is_valid_json_on_disk(store):
    store.update({"display.brightness": 12})
    store.save()
    with open(store.path) as handle:
        stored = json.load(handle)
    assert stored["display.brightness"] == 12


def test_save_is_atomic(store):
    """The board gets unplugged rather than shut down, so a half-written
    settings file must never be possible - and no temp files left behind."""
    store.update({"display.brightness": 50})
    store.save()
    store.update({"display.brightness": 51})
    store.save()

    directory = os.path.dirname(store.path)
    leftovers = [
        name for name in os.listdir(directory) if name.startswith(".settings-")
    ]
    assert leftovers == []


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json")
    store = Settings(path=str(path), autosave=False)
    assert store.get("display.brightness") == 75


def test_non_object_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]")
    assert Settings(path=str(path), autosave=False).get("display.brightness") == 75


def test_unknown_and_bad_stored_keys_are_ignored(tmp_path):
    """A settings file written by a different build of the code must still
    load - it just keeps the defaults for anything it can't use."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "display.brightness": 20,
                "setting.that.was.removed": 5,
                "display.web_fps": "garbage",
            }
        )
    )
    store = Settings(path=str(path), autosave=False)
    assert store.get("display.brightness") == 20
    assert store.get("display.web_fps") == 30


def test_missing_file_is_not_an_error(tmp_path):
    store = Settings(path=str(tmp_path / "nope" / "settings.json"), autosave=False)
    assert store.get("display.brightness") == 75


def test_unwritable_path_does_not_raise(tmp_path):
    """On the Pi the matrix driver drops root mid-run, so saving can start
    failing at any point. That must degrade to 'settings still apply, they
    just don't persist' rather than taking the board down."""
    directory = tmp_path / "readonly"
    directory.mkdir()
    store = Settings(path=str(directory / "settings.json"), autosave=False)
    directory.chmod(0o500)
    try:
        assert store.save() is False
        # Still fully usable in memory.
        store.update({"display.brightness": 5})
        assert store.get("display.brightness") == 5
    finally:
        directory.chmod(0o700)


def test_reset_restores_every_default(store):
    store.update({"display.brightness": 5, "clock.units": "celsius", "ball.size": 9})
    store.reset()
    for setting in SCHEMA:
        assert store.get(setting.key) == setting.default


def test_listeners_see_only_what_changed(store):
    seen = []
    store.subscribe(seen.append)

    store.update({"display.brightness": 60})
    assert seen == [{"display.brightness": 60}]

    # Re-applying the same value isn't a change, so no notification.
    store.update({"display.brightness": 60})
    assert len(seen) == 1


def test_a_broken_listener_cannot_break_updates(store):
    def explode(_changed):
        raise RuntimeError("boom")

    store.subscribe(explode)
    store.update({"display.brightness": 60})
    assert store.get("display.brightness") == 60


def test_generation_advances_on_change(store):
    before = store.generation
    store.update({"display.brightness": 60})
    assert store.generation > before

    unchanged = store.generation
    store.update({"display.brightness": 60})
    assert store.generation == unchanged


def test_concurrent_updates_and_reads_stay_consistent(store):
    """Readers are lock-free by design, so a reader must never see a torn or
    missing value while writers are running."""
    stop = threading.Event()
    errors = []

    def reader():
        try:
            while not stop.is_set():
                assert 1 <= store.get("display.brightness") <= 100
                assert len(store.as_dict()) == len(SCHEMA)
        except Exception as err:  # pragma: no cover - only on failure
            errors.append(err)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    for value in range(1, 101):
        store.update({"display.brightness": value})
    stop.set()
    for thread in readers:
        thread.join(timeout=5)

    assert errors == []
    assert store.get("display.brightness") == 100


def test_autosave_debounces_and_eventually_writes(tmp_path):
    """Dragging a slider fires a change per pixel of travel; those have to
    collapse into one write, but the last value must still reach disk."""
    import controller.settings as settings_module

    path = tmp_path / "settings.json"
    store = Settings(path=str(path), autosave=True)
    original = settings_module.SAVE_DEBOUNCE_SECONDS
    settings_module.SAVE_DEBOUNCE_SECONDS = 0.15
    try:
        for value in range(10, 40):
            store.update({"display.brightness": value})
        assert not path.exists(), "debounce should have deferred every write"

        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert path.exists(), "debounced save never fired"
        with open(path) as handle:
            assert json.load(handle)["display.brightness"] == 39
    finally:
        settings_module.SAVE_DEBOUNCE_SECONDS = original


def test_prepare_settings_file_is_a_noop_when_not_root(tmp_path):
    """On a dev machine there's no privilege drop to prepare for, and this
    must not touch anything or raise."""
    path = tmp_path / "settings.json"
    prepare_settings_file(str(path))
    assert not path.exists()


def test_the_settings_page_stays_scannable():
    """The page is for nudging one slider, not for reading.

    Most rows carry no prose at all, and the few hints that survive are short
    enough to sit on one line next to the label.
    """
    with_help = [setting for setting in SCHEMA if setting.help]

    assert len(with_help) < len(SCHEMA) / 3, "too many rows carry prose"
    for setting in with_help:
        assert len(setting.help) <= 40, f"{setting.key}: {setting.help!r}"
        assert "\n" not in setting.help


def test_schema_json_no_longer_ships_per_section_paragraphs():
    payload = Settings(path="/dev/null", autosave=False).schema_json()
    assert set(payload) == {"sections", "settings"}


def test_every_setting_still_has_a_label_and_section():
    # Losing the help text is fine; losing the label would leave a bare
    # slider with no way to tell what it does.
    for setting in SCHEMA:
        assert setting.label.strip()
        assert setting.section.strip()
