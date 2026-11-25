import pytest

from learnova.home import HOME_ENV


@pytest.fixture(autouse=True)
def learnova_home(tmp_path, monkeypatch):
    """Every test gets its own user home, so the real ~/.learnova is never touched."""
    home = tmp_path / "learnova-home"
    monkeypatch.setenv(HOME_ENV, str(home))
    return home
