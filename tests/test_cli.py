"""Tests for CLI configuration resolution."""

from gtl.cli import get_config_value


class TestGetConfigValue:
    def test_cli_argument_wins(self, monkeypatch):
        monkeypatch.setenv("GTL_LOCATION", "US")
        value = get_config_value("EU", "GTL_LOCATION", "location", {"location": "US"})
        assert value == "EU"

    def test_env_var_beats_config_file(self, monkeypatch):
        monkeypatch.setenv("GTL_LOCATION", "EU")
        value = get_config_value(None, "GTL_LOCATION", "location", {"location": "US"})
        assert value == "EU"

    def test_config_file_beats_default(self, monkeypatch):
        monkeypatch.delenv("GTL_LOCATION", raising=False)
        value = get_config_value(
            None, "GTL_LOCATION", "location", {"location": "EU"}, default="US"
        )
        assert value == "EU"

    def test_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("GTL_LOCATION", raising=False)
        assert get_config_value(None, "GTL_LOCATION", "location", {}, "US") == "US"

    def test_absent_everywhere_is_none(self, monkeypatch):
        monkeypatch.delenv("GTL_LOCATION", raising=False)
        assert get_config_value(None, "GTL_LOCATION", "location", {}) is None
