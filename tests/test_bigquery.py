"""Tests for BigQuery helpers that need no credentials."""

from unittest.mock import MagicMock

import pytest
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from gtl import bigquery as bq


def make_client(project="proj", dataset="ds", location=None, existing_location=None):
    """A mock client whose dataset either exists (in existing_location) or not."""
    client = MagicMock()
    client.project = project
    # MagicMock auto-attributes are truthy, which would mask a missing location
    client.location = location
    client.dataset.return_value = bigquery.DatasetReference(project, dataset)

    if existing_location is None:
        client.get_dataset.side_effect = NotFound("no such dataset")
    else:
        found = bigquery.Dataset(bigquery.DatasetReference(project, dataset))
        found._properties["location"] = existing_location
        client.get_dataset.return_value = found

    return client


class TestSanitizeIdentifier:
    def test_replaces_characters_invalid_in_a_table_name(self):
        assert (
            bq.sanitize_identifier("github.com/example-org/example-repo")
            == "github_com_example_org_example_repo"
        )

    def test_leaves_a_clean_identifier_alone(self):
        assert bq.sanitize_identifier("main_branch_1") == "main_branch_1"


class TestEnsureDataset:
    def test_creates_missing_dataset_in_requested_location(self):
        client = make_client()

        assert bq.ensure_dataset(client, "ds", "EU") == "EU"

        created = client.create_dataset.call_args[0][0]
        assert created.location == "EU"

    def test_creates_missing_dataset_in_client_location(self):
        client = make_client(location="europe-west3")

        assert bq.ensure_dataset(client, "ds") == "europe-west3"
        assert client.create_dataset.call_args[0][0].location == "europe-west3"

    def test_falls_back_to_us_when_nothing_is_configured(self):
        client = make_client()

        assert bq.ensure_dataset(client, "ds") == bq.DEFAULT_LOCATION
        assert client.create_dataset.call_args[0][0].location == "US"

    def test_reports_existing_location_when_none_requested(self):
        client = make_client(existing_location="EU")

        assert bq.ensure_dataset(client, "ds") == "EU"
        client.create_dataset.assert_not_called()

    def test_accepts_matching_location_case_insensitively(self):
        client = make_client(existing_location="EU")

        assert bq.ensure_dataset(client, "ds", "eu") == "EU"
        client.create_dataset.assert_not_called()

    def test_rejects_a_conflicting_explicit_location(self):
        client = make_client(existing_location="US")

        with pytest.raises(ValueError, match="cannot move a dataset"):
            bq.ensure_dataset(client, "ds", "EU")

        client.create_dataset.assert_not_called()


class TestLoadRows:
    def test_skips_the_job_entirely_when_there_are_no_rows(self):
        client = MagicMock()

        bq.load_rows(client, "proj.ds.commits", bq.TABLE_SCHEMAS["commits"], [])

        client.load_table_from_json.assert_not_called()

    def test_appends_with_the_table_schema_and_waits(self):
        client = MagicMock()
        rows = [{"repo_id": "r", "sha": "abc"}]

        bq.load_rows(client, "proj.ds.commits", bq.TABLE_SCHEMAS["commits"], rows)

        args, kwargs = client.load_table_from_json.call_args
        assert args[0] == rows
        assert args[1] == "proj.ds.commits"
        job_config = kwargs["job_config"]
        assert job_config.schema == bq.TABLE_SCHEMAS["commits"]
        assert job_config.write_disposition == bigquery.WriteDisposition.WRITE_APPEND
        # A load job that is never waited on can fail silently
        client.load_table_from_json.return_value.result.assert_called_once()


class TestTableSchemas:
    def test_every_table_the_sync_writes_to_has_a_schema(self):
        assert set(bq.TABLE_SCHEMAS) == {
            "repositories",
            "branches",
            "commits",
            "file_changes",
            "current_files",
        }
