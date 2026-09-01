# GTL - Git-Transform-Load

Sync Git repository history to BigQuery.

## Installation

The name `gtl` on PyPI belongs to an unrelated project, so `pip install gtl`
installs the wrong package. Install from git instead:

```bash
pip install git+https://github.com/veezoo-ai/gtl.git
```

## Usage

### Initialize Schema

Run once per BigQuery dataset to create the required tables:

```bash
gtl init --project=my-project --dataset=git_repo --location=EU
```

### Sync Repository

Sync the current repository to BigQuery:

```bash
gtl sync --project=my-project --dataset=git_repo
```

Options:
- `--location`: BigQuery location of the dataset (see below)
- `--repo-id`: Override auto-detected repository identifier
- `--branch`: Specific branch to sync (defaults to current branch)
- `--all-branches`: Sync all branches in the repository
- `--max-file-size`: Maximum file size in bytes (default: 102400)
- `--max-diff-size`: Truncate each diff to this many bytes, 0 to disable (default: 1048576)
- `-v, --verbose`: Print verbose output

### Location

A BigQuery job must run in the same location as the dataset it touches, and
BigQuery cannot join across locations or move a dataset after the fact — so the
location has to be right the first time.

```bash
# Create the dataset in the EU
gtl init --project=my-project --dataset=git_repo --location=EU
```

- When `--location` is given, the dataset is created there. If the dataset
  already exists somewhere else, gtl fails with an explicit error rather than
  writing to the wrong region.
- When it is omitted, gtl adopts the location of the existing dataset, so a
  dataset created elsewhere (or before `--location` existed) keeps working
  without the flag.
- If the dataset does not exist and no location is given, it is created in `US`.

Any BigQuery location works: multi-regions (`EU`, `US`) and single regions
(`europe-west3`, `us-central1`, …).

### Branch Syncing

By default, GTL syncs the currently checked out branch. You can specify a different branch or sync all branches:

```bash
# Sync a specific branch
gtl sync --project=my-project --dataset=git_repo --branch=develop

# Sync all branches
gtl sync --project=my-project --dataset=git_repo --all-branches
```

## Configuration

GTL supports configuration via (in priority order):

1. Command-line arguments
2. Environment variables
3. `.gtl.yaml` config file

| CLI Arg | Env Var | Config Key | Description |
|---------|---------|------------|-------------|
| `--project` | `GTL_PROJECT` | `project` | GCP project ID |
| `--dataset` | `GTL_DATASET` | `dataset` | BigQuery dataset name |
| `--location` | `GTL_LOCATION` | `location` | BigQuery location (e.g. `EU`) |
| `--repo-id` | `GTL_REPO_ID` | `repo_id` | Repository identifier |
| `--branch` | `GTL_BRANCH` | `branch` | Branch to sync |
| `--max-file-size` | `GTL_MAX_FILE_SIZE` | `max_file_size` | Max file size (default: 102400) |
| `--max-diff-size` | `GTL_MAX_DIFF_SIZE` | `max_diff_size` | Max diff size (default: 1048576, 0 disables) |

Example `.gtl.yaml`:

```yaml
project: my-project
dataset: git_repo
location: EU
branch: main
max_file_size: 102400
```

## GitHub Actions

```yaml
name: Sync to BigQuery

on:
  push:
    branches: [main, develop]

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # gtl needs full history

      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - run: |
          pip install git+https://github.com/veezoo-ai/gtl.git
          gtl sync --project=my-project --dataset=git_repo --location=EU --branch=${{ github.ref_name }}
```

Note that a push made by a workflow using the default `GITHUB_TOKEN` does not
trigger other workflows. If the commits you want to sync are produced by
another workflow, run `gtl sync` as a step in *that* workflow rather than in a
separate one keyed on `push`.

To sync all branches on a schedule:

```yaml
name: Sync All Branches to BigQuery

on:
  schedule:
    - cron: '0 0 * * *'  # Daily at midnight

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - run: |
          pip install git+https://github.com/veezoo-ai/gtl.git
          gtl sync --project=my-project --dataset=git_repo --location=EU --all-branches
```

## BigQuery Schema

### repositories
Tracks synced repositories.

### branches
Tracks branches for each repository with their HEAD SHA.

### commits
Stores commit metadata (sha, branch, author, timestamp, message).

### file_changes
Stores per-file diffs for each commit.

### current_files
Stores current file contents per branch (updated on each sync).

Rows are written with load jobs rather than the legacy streaming API. Streamed
rows sit in a buffer that `UPDATE`, `DELETE` and `MERGE` cannot reach for some
time after the write, and gtl issues exactly those statements immediately after
writing. Load jobs also carry far larger size limits and are free.

Commits are written in batches, and each batch advances the branch HEAD only
once its rows are durable — so an interrupted sync resumes from the last
completed batch instead of re-inserting commits it already wrote.

## Python API

```python
import gtl

gtl.init_schema(project="my-project", dataset="git_repo", location="EU")
gtl.sync_repository(project="my-project", dataset="git_repo", branch="main")
```

The convenience functions are named `sync_repository` and `init_schema` rather
than `sync` and `init` so they do not shadow the `gtl.sync` submodule. The
underlying functions remain `gtl.sync.sync` and `gtl.sync.init`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite runs without GCP credentials: it drives a full sync against a
throwaway git repository and an in-memory stand-in for BigQuery.

## License

MIT
