"""Tests for sync orchestration that need no BigQuery."""

from unittest.mock import MagicMock

from gtl import sync as sync_mod


class TestConnect:
    def test_binds_the_client_to_an_explicit_location(self, monkeypatch):
        client = MagicMock()
        client.location = "EU"
        get_client = MagicMock(return_value=client)
        monkeypatch.setattr(sync_mod.bq, "get_client", get_client)
        monkeypatch.setattr(sync_mod.bq, "ensure_schema", MagicMock(return_value="EU"))

        result, location = sync_mod.connect("proj", "ds", "EU")

        assert location == "EU"
        assert result is client
        # Already bound correctly, so no second client
        get_client.assert_called_once_with("proj", "EU")

    def test_rebinds_the_client_to_a_discovered_location(self, monkeypatch):
        unbound = MagicMock()
        unbound.location = None
        rebound = MagicMock()
        rebound.location = "EU"
        get_client = MagicMock(side_effect=[unbound, rebound])
        monkeypatch.setattr(sync_mod.bq, "get_client", get_client)
        monkeypatch.setattr(sync_mod.bq, "ensure_schema", MagicMock(return_value="EU"))

        result, location = sync_mod.connect("proj", "ds")

        # Without this the client would run its jobs in the US against an EU
        # dataset, which BigQuery rejects
        assert location == "EU"
        assert result is rebound
        assert get_client.call_args_list[-1][0] == ("proj", "EU")
