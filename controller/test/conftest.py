import pytest

from controller.settings import settings as shared_settings


@pytest.fixture(autouse=True)
def reset_settings():
    """`controller.settings.settings` is a process-wide singleton, so a test
    that changes a setting would otherwise leak into every test after it.
    Autosave is off for the duration so no test ever touches the real file."""
    shared_settings._autosave = False
    shared_settings.reset()
    yield shared_settings
    shared_settings.reset()
    shared_settings._autosave = True
