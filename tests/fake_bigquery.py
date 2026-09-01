"""An in-memory stand-in for a BigQuery client.

It emulates only what gtl actually issues: the handful of parameterised
SELECTs, the branch-head UPDATE, and the current_files MERGE. That is enough to
run a whole sync and assert on the rows that land.
"""

import re
from types import SimpleNamespace

from google.cloud.exceptions import NotFound


def _table_key(table) -> str:
    """Normalise a table id string or Table object to 'project.dataset.name'."""
    if isinstance(table, str):
        return table
    return f"{table.project}.{table.dataset_id}.{table.table_id}"


class FakeJob:
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else []

    def result(self):
        return self._rows


class FakeClient:
    """Stores rows per table and answers gtl's queries from them."""

    def __init__(self, project="proj", location=None):
        self.project = project
        self.location = location
        self.tables: dict[str, list[dict]] = {}
        self.datasets: dict[str, str] = {}
        self.load_jobs: list[tuple[str, int]] = []
        self.queries: list[str] = []

    # -- datasets -----------------------------------------------------
    def dataset(self, dataset_id):
        from google.cloud import bigquery

        return bigquery.DatasetReference(self.project, dataset_id)

    def get_dataset(self, dataset_ref):
        from google.cloud import bigquery

        name = dataset_ref.dataset_id
        if name not in self.datasets:
            raise NotFound(f"dataset {name}")
        ds = bigquery.Dataset(bigquery.DatasetReference(self.project, name))
        ds._properties["location"] = self.datasets[name]
        return ds

    def create_dataset(self, ds):
        self.datasets[ds.dataset_id] = ds.location

    # -- tables -------------------------------------------------------
    def get_table(self, table):
        key = _table_key(table)
        if key not in self.tables:
            raise NotFound(f"table {key}")
        return SimpleNamespace(table_id=key)

    def create_table(self, table):
        self.tables.setdefault(_table_key(table), [])

    def delete_table(self, table, not_found_ok=False):
        key = _table_key(table)
        if key in self.tables:
            del self.tables[key]
        elif not not_found_ok:
            raise NotFound(f"table {key}")

    def load_table_from_json(self, rows, table, job_config=None):
        key = _table_key(table)
        self.tables.setdefault(key, []).extend(rows)
        self.load_jobs.append((key, len(rows)))
        return FakeJob()

    # -- queries ------------------------------------------------------
    def query(self, sql, job_config=None):
        self.queries.append(sql)
        params = {}
        if job_config is not None and job_config.query_parameters:
            params = {p.name: p.value for p in job_config.query_parameters}

        target = re.search(r"`([\w.\-]+)`", sql).group(1)
        rows = self.tables.setdefault(target, [])

        if sql.lstrip().startswith("MERGE"):
            return FakeJob(self._merge(sql, target, params))
        if sql.lstrip().startswith("UPDATE"):
            return FakeJob(self._update_branch_head(rows, params))
        return FakeJob(self._select(sql, rows, params))

    def _select(self, sql, rows, params):
        # Read the column each parameter filters on straight out of the SQL:
        # repositories filters `id = @repo_id`, branches filters
        # `name = @branch_name`, so a name-based guess would silently mismatch.
        matched = [
            r
            for r in rows
            if all(r.get(col) == params[param] for col, param in _predicates(sql))
        ]
        if "ORDER BY committed_at DESC" in sql:
            matched.sort(key=lambda r: r["committed_at"], reverse=True)
        if "LIMIT 1" in sql:
            matched = matched[:1]
        return [SimpleNamespace(**r) for r in matched]

    def _update_branch_head(self, rows, params):
        for r in rows:
            if r["repo_id"] == params["repo_id"] and r["name"] == params["branch_name"]:
                r["head_sha"] = params["head_sha"]
                r["updated_at"] = params["updated_at"]
        return []

    def _merge(self, sql, target, params):
        source_key = re.findall(r"`([\w.\-]+)`", sql)[1]
        source = self.tables.get(source_key, [])
        keep = [
            r
            for r in self.tables[target]
            if not (
                r["repo_id"] == params["repo_id"]
                and ("branch" not in params or r.get("branch") == params["branch"])
            )
        ]
        self.tables[target] = keep + [dict(r) for r in source]
        return []


def _predicates(sql: str) -> list[tuple[str, str]]:
    """Extract (column, parameter) pairs from a WHERE clause."""
    return re.findall(r"(\w+)\s*=\s*@(\w+)", sql.split("WHERE", 1)[-1])
