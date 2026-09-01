# Technical Briefing: gtl (Git-Transform-Load)

## Project Overview

Build a pip-installable Python package called `gtl` that syncs Git repository history to BigQuery. The package will be maintained in its own repository and can be installed into any project that needs to sync its git history. The schema supports multiple repositories in a single BigQuery dataset.

## Requirements

- Standalone pip-installable package (hosted in its own repo)
- Support multiple git repositories in a single BigQuery dataset
- Track commits on any branch (configurable)
- Support syncing all branches or a specific branch
- Store per-file diffs for each commit
- Store current file contents per branch (not historical versions)
- Skip binary files entirely
- Import full repository history on first run
- Incremental updates on subsequent runs

## Architecture

```
gtl (pip package)
    ↓ install
any-repo/.github/workflows/sync.yml
    ↓ push to master
GitHub Actions
    ↓ gtl sync
BigQuery (shared dataset, multiple repos)
```

## BigQuery Schema

### Table: repositories

Tracks each repository being synced.

| Column | Type | Description |
|--------|------|-------------|
| id | STRING | Unique identifier (e.g., "github.com/org/repo") |
| name | STRING | Repository name |
| url | STRING | Repository URL |
| created_at | TIMESTAMP | When the repo was first synced |

### Table: branches

Tracks branches for each repository.

| Column | Type | Description |
|--------|------|-------------|
| repo_id | STRING | References repositories.id |
| name | STRING | Branch name |
| head_sha | STRING | Current HEAD SHA of the branch |
| is_default | BOOL | Whether this is the default branch |
| created_at | TIMESTAMP | When the branch was first synced |
| updated_at | TIMESTAMP | When the branch was last synced |

### Table: commits

Stores commit metadata.

| Column | Type | Description |
|--------|------|-------------|
| repo_id | STRING | References repositories.id |
| sha | STRING | Commit SHA |
| branch | STRING | Branch this commit was synced from |
| author_name | STRING | Name of the commit author |
| author_email | STRING | Email of the commit author |
| committed_at | TIMESTAMP | When the commit was made |
| message | STRING | Commit message |
| parent_sha | STRING | SHA of the parent commit (null for initial) |
| ingested_at | TIMESTAMP | When the record was inserted |

### Table: file_changes

Stores per-file diffs for each commit.

| Column | Type | Description |
|--------|------|-------------|
| repo_id | STRING | References repositories.id |
| commit_sha | STRING | References commits.sha |
| file_path | STRING | Full path of the file |
| change_type | STRING | Type of change: "A" (added), "M" (modified), "D" (deleted), "R" (renamed) |
| old_path | STRING | Previous path if renamed, null otherwise |
| diff | STRING | Diff for this file in this commit |
| additions | INT64 | Lines added |
| deletions | INT64 | Lines deleted |
| ingested_at | TIMESTAMP | When the record was inserted |

### Table: current_files

Stores current state of files per branch (overwritten on each sync).

| Column | Type | Description |
|--------|------|-------------|
| repo_id | STRING | References repositories.id |
| file_path | STRING | Full path of the file |
| branch | STRING | Branch this file belongs to |
| content | STRING | Current file contents |
| size_bytes | INT64 | Size of the file in bytes |
| last_commit_sha | STRING | SHA of last commit that touched this file |
| updated_at | TIMESTAMP | When this record was last updated |

## Package Structure

```
gtl/
├── pyproject.toml
├── README.md
├── src/
│   └── gtl/
│       ├── __init__.py
│       ├── cli.py          # CLI entry point
│       ├── sync.py         # Core sync logic
│       ├── git.py          # Git operations
│       ├── bigquery.py     # BigQuery operations
│       └── schema.sql      # Schema definitions
└── tests/
    └── ...
```

## CLI Interface

```bash
# Initialize schema (run once per dataset)
gtl init --project=my-project --dataset=git_repo --location=EU

# Sync current branch
gtl sync --project=my-project --dataset=git_repo

# Sync a specific branch
gtl sync --project=my-project --dataset=git_repo --branch=develop

# Sync all branches
gtl sync --project=my-project --dataset=git_repo --all-branches

# Sync with custom repo ID
gtl sync --project=my-project --dataset=git_repo --repo-id=github.com/org/repo

# Sync with custom file size limit (default 100KB)
gtl sync --project=my-project --dataset=git_repo --max-file-size=50000

# Truncate individual diffs (default 1MB, 0 disables)
gtl sync --project=my-project --dataset=git_repo --max-diff-size=0
```

## Configuration

The CLI should support configuration via:

1. Command-line arguments (highest priority)
2. Environment variables
3. `.gtl.yaml` config file in repo root (lowest priority)

| CLI Arg | Env Var | Config Key | Description |
|---------|---------|------------|-------------|
| `--project` | `GTL_PROJECT` | `project` | GCP project ID |
| `--dataset` | `GTL_DATASET` | `dataset` | BigQuery dataset name |
| `--repo-id` | `GTL_REPO_ID` | `repo_id` | Repository identifier (auto-detected from git remote if not set) |
| `--branch` | `GTL_BRANCH` | `branch` | Branch to sync (defaults to current branch) |
| `--location` | `GTL_LOCATION` | `location` | BigQuery location, e.g. `EU` (default: location of an existing dataset, else `US`) |
| `--max-file-size` | `GTL_MAX_FILE_SIZE` | `max_file_size` | Max file size in bytes (default: 102400) |
| `--max-diff-size` | `GTL_MAX_DIFF_SIZE` | `max_diff_size` | Max size of a single diff in bytes (default: 1048576, 0 disables) |

## Core Functions

### git.py

```python
def get_repo_id() -> str:
    """Auto-detect repo ID from git remote origin URL."""

def get_current_branch() -> str | None:
    """Get the currently checked out branch name."""

def get_branches(remote: bool = False) -> list[str]:
    """Get list of branches (local or remote)."""

def get_default_branch() -> str:
    """Get the default branch name (main or master)."""

def get_branch_head_sha(branch: str) -> str | None:
    """Get the HEAD SHA of a branch."""

def get_new_commits(last_sha: str | None, branch: str | None = None) -> list[dict]:
    """Get commits since last_sha with metadata for a specific branch."""

def get_file_changes(sha: str, parent_sha: str | None) -> list[dict]:
    """Get per-file diffs for a commit. Returns list with:
    - file_path, change_type, old_path, diff, additions, deletions
    """

def get_current_files(max_size: int, branch: str | None = None) -> list[dict]:
    """Get all current text files in repo with contents for a specific branch."""

def is_binary(data: bytes) -> bool:
    """Check if content is binary (has null bytes)."""

def run_git_bytes(*args: str) -> bytes | None:
    """Run git and return raw stdout. File contents must not be stripped or
    decoded as text -- stripping loses trailing newlines, and decoding raises
    on binary files before is_binary can reject them."""

def truncate_diff(diff: str, max_diff_size: int) -> str:
    """Truncate an oversized diff on a byte boundary. 0 disables."""
```

### bigquery.py

```python
def get_client(project: str, location: str | None = None):
    """Create a BigQuery client bound to a location."""

def load_rows(client, table_id: str, schema: list, rows: list[dict]):
    """Append rows with a load job. Not the streaming API: streamed rows sit in
    a buffer that UPDATE/MERGE cannot reach."""

def ensure_dataset(client, dataset: str, location: str | None = None) -> str:
    """Create the dataset if absent; return the location it lives in."""

def ensure_schema(client, dataset: str, location: str | None = None) -> str:
    """Create tables if they don't exist; return the dataset location."""

def ensure_repo(client, dataset: str, repo_id: str, name: str, url: str):
    """Insert or update repository record."""

def ensure_branch(client, dataset: str, repo_id: str, branch_name: str, is_default: bool = False):
    """Insert or update branch record."""

def update_branch_head(client, dataset: str, repo_id: str, branch_name: str, head_sha: str):
    """Update the HEAD SHA for a branch."""

def get_last_commit_sha(client, dataset: str, repo_id: str, branch: str | None = None) -> str | None:
    """Get most recent processed commit SHA for a repo/branch."""

def get_branch_head_sha(client, dataset: str, repo_id: str, branch: str) -> str | None:
    """Get the HEAD SHA for a branch from the branches table."""

def insert_commits(client, dataset: str, commits: list[dict]):
    """Batch insert commits (with branch field)."""

def insert_file_changes(client, dataset: str, changes: list[dict]):
    """Batch insert file changes."""

def upsert_current_files(client, dataset: str, repo_id: str, files: list[dict], branch: str | None = None):
    """Replace current files for a repo/branch using MERGE."""
```

### sync.py

```python
def connect(project: str, dataset: str, location: str | None = None):
    """Client + schema, with the client bound to the dataset's location."""

def sync(project: str, dataset: str, location: str | None, repo_id: str, branch: str | None, all_branches: bool, max_file_size: int, max_diff_size: int):
    """Main sync orchestration:
    1. Determine which branches to sync
    2. For each branch, call sync_branch
    """

def sync_branch(client, dataset: str, repo_id: str, branch: str, max_file_size: int):
    """Sync a specific branch:
    1. Get last processed commit for the branch
    2. Get new commits since then
    3. For each commit, get file changes and insert
    4. Update current_files with latest state for the branch
    """
```

## Sync Logic

```
1. Determine branches to sync (current, specific, or all)
2. For each branch:
   a. Get last_sha from BigQuery for this repo/branch
   b. Get new commits since last_sha (or all if first run)
   c. For each commit (oldest first):
      i. Insert commit record with branch
      ii. Get per-file diffs
      iii. Insert file_changes records
   d. Update branch HEAD SHA
   e. After all commits processed:
      i. Get current files from branch
      ii. MERGE into current_files table for this branch (delete removed, upsert existing)
```

## GitHub Actions Usage

In any repo that wants to sync:

### .github/workflows/sync-to-bigquery.yml

```yaml
name: Sync to BigQuery

on:
  push:
    branches: [main, develop, feature/*]

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
          gtl sync --project=my-project --dataset=git_repo --branch=${{ github.ref_name }}
```

### Syncing All Branches on a Schedule

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
          gtl sync --project=my-project --dataset=git_repo --all-branches
```

Or with config file:

### .gtl.yaml

```yaml
project: my-project
dataset: git_repo
location: EU
branch: main
max_file_size: 102400
```

```yaml
# Simplified workflow
- run: |
    pip install git+https://github.com/veezoo-ai/gtl.git
    gtl sync
```

## Setup Steps

### 1. Create the gtl Package Repository

1. Create new repo `gtl`
2. Implement package structure as described above
3. Publish to PyPI or private package index

### 2. GCP Setup (One-time)

```bash
# Create service account
gcloud iam service-accounts create gtl-sync \
  --display-name="GTL BigQuery Sync"

# Grant BigQuery access
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:gtl-sync@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

# Generate key
gcloud iam service-accounts keys create key.json \
  --iam-account=gtl-sync@${PROJECT_ID}.iam.gserviceaccount.com
```

### 3. Initialize BigQuery Schema

```bash
pip install git+https://github.com/veezoo-ai/gtl.git
gtl init --project=my-project --dataset=git_repo
```

### 4. Configure Each Repository

1. Add `GCP_SA_KEY` secret to repo (Settings → Secrets → Actions)
2. Add workflow file `.github/workflows/sync-to-bigquery.yml`
3. Optionally add `.gtl.yaml` for config

## Example Queries

**Get all commits for a repo:**
```sql
SELECT sha, author_name, committed_at, message
FROM `project.git_repo.commits`
WHERE repo_id = 'github.com/org/repo'
ORDER BY committed_at DESC;
```

**See what files changed in a commit:**
```sql
SELECT file_path, change_type, additions, deletions
FROM `project.git_repo.file_changes`
WHERE repo_id = 'github.com/org/repo'
  AND commit_sha = 'abc123...'
ORDER BY file_path;
```

**View diff for a specific file in a commit:**
```sql
SELECT diff
FROM `project.git_repo.file_changes`
WHERE repo_id = 'github.com/org/repo'
  AND commit_sha = 'abc123...'
  AND file_path = 'src/main.py';
```

**Get history of changes to a file:**
```sql
SELECT c.sha, c.author_name, c.committed_at, c.message, 
       f.change_type, f.additions, f.deletions
FROM `project.git_repo.commits` c
JOIN `project.git_repo.file_changes` f 
  ON c.repo_id = f.repo_id AND c.sha = f.commit_sha
WHERE c.repo_id = 'github.com/org/repo'
  AND f.file_path = 'src/main.py'
ORDER BY c.committed_at DESC;
```

**Current contents of a file:**
```sql
SELECT content
FROM `project.git_repo.current_files`
WHERE repo_id = 'github.com/org/repo'
  AND file_path = 'README.md';
```

**Find largest current files across all repos:**
```sql
SELECT repo_id, file_path, size_bytes
FROM `project.git_repo.current_files`
ORDER BY size_bytes DESC
LIMIT 20;
```

**Most frequently changed files:**
```sql
SELECT file_path, COUNT(*) as change_count, 
       SUM(additions) as total_additions,
       SUM(deletions) as total_deletions
FROM `project.git_repo.file_changes`
WHERE repo_id = 'github.com/org/repo'
GROUP BY file_path
ORDER BY change_count DESC
LIMIT 20;
```

**Activity across all repos:**
```sql
SELECT 
  repo_id,
  DATE(committed_at) as date,
  COUNT(*) as commits
FROM `project.git_repo.commits`
GROUP BY 1, 2
ORDER BY 2 DESC, 3 DESC;
```

**List all branches for a repo:**
```sql
SELECT name, head_sha, is_default, updated_at
FROM `project.git_repo.branches`
WHERE repo_id = 'github.com/org/repo'
ORDER BY updated_at DESC;
```

**Get commits for a specific branch:**
```sql
SELECT sha, author_name, committed_at, message
FROM `project.git_repo.commits`
WHERE repo_id = 'github.com/org/repo'
  AND branch = 'develop'
ORDER BY committed_at DESC;
```

**Compare commit counts across branches:**
```sql
SELECT 
  branch,
  COUNT(*) as commit_count,
  MIN(committed_at) as first_commit,
  MAX(committed_at) as last_commit
FROM `project.git_repo.commits`
WHERE repo_id = 'github.com/org/repo'
GROUP BY branch
ORDER BY commit_count DESC;
```

**Get current file contents from a specific branch:**
```sql
SELECT file_path, content, size_bytes
FROM `project.git_repo.current_files`
WHERE repo_id = 'github.com/org/repo'
  AND branch = 'feature/new-ui'
ORDER BY file_path;
```

## Notes

- Repo ID is auto-detected from `git remote get-url origin` if not specified
- Branch defaults to current branch if not specified
- First sync processes entire history; subsequent syncs are incremental
- Binary detection uses null-byte check in first 8KB
- `current_files` is maintained per-branch (MERGE with delete for removed files)
- Each branch's sync state is tracked independently via the `branches` table
- Per-file diffs may be large; `--max-diff-size` truncates them (default 1MB)
- Rows are written with load jobs, not the streaming API: streamed rows sit in a
  buffer that `UPDATE`/`MERGE` cannot reach, which would break `update_branch_head`
  and `upsert_current_files`
- A BigQuery job must run in the dataset's location, and locations cannot be joined
  across or changed later, so `--location` is set at `init` time
- Renames are tracked with change_type "R" and old_path populated
