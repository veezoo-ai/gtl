"""Core sync logic for gtl."""

from . import git
from . import bigquery as bq

# Default cap on a single file's diff, in bytes. Generous enough for ordinary
# changes, small enough that a wholesale file rewrite cannot blow a row past
# BigQuery's limits.
DEFAULT_MAX_DIFF_SIZE = 1048576

# Commits are written in batches rather than one API call at a time. Each
# flush also advances the branch head, so an interrupted sync resumes from the
# last durable batch instead of re-inserting commits it already wrote.
COMMIT_BATCH_SIZE = 100


def connect(
    project: str,
    dataset: str,
    location: str | None = None,
) -> tuple[object, str]:
    """Create a client and make sure the dataset and tables exist.

    BigQuery jobs must run in the same location as the dataset they touch. When
    no location is configured we let an existing dataset tell us where it lives
    and rebind the client to it, so a dataset created outside gtl (or before
    --location existed) keeps working without extra flags.

    Returns:
        (client, location) with the client bound to the dataset's location.
    """
    client = bq.get_client(project, location)
    resolved_location = bq.ensure_schema(client, dataset, location)

    if client.location != resolved_location:
        client = bq.get_client(project, resolved_location)

    return client, resolved_location


def sync(
    project: str,
    dataset: str,
    location: str | None = None,
    repo_id: str | None = None,
    branch: str | None = None,
    all_branches: bool = False,
    max_file_size: int = 102400,
    max_diff_size: int = DEFAULT_MAX_DIFF_SIZE,
    verbose: bool = False,
) -> dict:
    """Main sync orchestration.

    1. Get last processed commit
    2. Get new commits since then
    3. For each commit, get file changes and insert
    4. Update current_files with latest state

    Args:
        project: GCP project ID
        dataset: BigQuery dataset name
        location: BigQuery location (e.g. "EU"). Auto-detected from an
            existing dataset when not given.
        repo_id: Repository identifier (auto-detected if not provided)
        branch: Specific branch to sync (defaults to current branch)
        all_branches: Sync all branches instead of just one
        max_file_size: Maximum file size in bytes (default: 100KB)
        max_diff_size: Maximum size of a single diff in bytes (0 disables)
        verbose: Print progress information

    Returns:
        dict with sync statistics
    """
    # Auto-detect repo info if not provided
    if not repo_id:
        repo_id = git.get_repo_id()
        if not repo_id:
            raise ValueError(
                "Could not auto-detect repository ID (no git remote origin). "
                "Please specify --repo-id explicitly."
            )

    repo_name = git.get_repo_name(repo_id)
    repo_url = git.get_repo_url()

    if verbose:
        print(f"Syncing repository: {repo_id}")

    # Initialize BigQuery client and ensure the schema exists
    client, resolved_location = connect(project, dataset, location)

    if verbose:
        print(f"Target: {project}.{dataset} ({resolved_location})")

    # Ensure repository record exists
    bq.ensure_repo(client, dataset, repo_id, repo_name, repo_url)

    # Determine which branches to sync
    if all_branches:
        branches_to_sync = git.get_branches(remote=False)
        if not branches_to_sync:
            # Fallback to remote branches
            branches_to_sync = git.get_branches(remote=True)
        if verbose:
            print(f"Syncing all branches: {', '.join(branches_to_sync)}")
    else:
        # Use specified branch or detect current branch
        if branch:
            branches_to_sync = [branch]
        else:
            current_branch = git.get_current_branch()
            if current_branch:
                branches_to_sync = [current_branch]
            else:
                # Fallback to default branch
                branches_to_sync = [git.get_default_branch()]
        if verbose:
            print(f"Syncing branch: {branches_to_sync[0]}")

    # Track overall statistics
    total_commits_processed = 0
    total_file_changes_processed = 0
    total_current_files_updated = 0
    branches_synced = []

    # Process each branch
    for branch_name in branches_to_sync:
        result = sync_branch(
            client=client,
            dataset=dataset,
            repo_id=repo_id,
            branch=branch_name,
            max_file_size=max_file_size,
            max_diff_size=max_diff_size,
            verbose=verbose,
        )
        total_commits_processed += result["commits_processed"]
        total_file_changes_processed += result["file_changes_processed"]
        total_current_files_updated += result["current_files_updated"]
        branches_synced.append(branch_name)

    if verbose:
        print("Sync complete!")

    return {
        "repo_id": repo_id,
        "location": resolved_location,
        "branches_synced": branches_synced,
        "commits_processed": total_commits_processed,
        "file_changes_processed": total_file_changes_processed,
        "current_files_updated": total_current_files_updated,
    }


def sync_branch(
    client,
    dataset: str,
    repo_id: str,
    branch: str,
    max_file_size: int = 102400,
    max_diff_size: int = DEFAULT_MAX_DIFF_SIZE,
    verbose: bool = False,
) -> dict:
    """Sync a specific branch.

    Args:
        client: BigQuery client
        dataset: BigQuery dataset name
        repo_id: Repository identifier
        branch: Branch name to sync
        max_file_size: Maximum file size in bytes
        max_diff_size: Maximum size of a single diff in bytes (0 disables)
        verbose: Print progress information

    Returns:
        dict with sync statistics for this branch
    """
    if verbose:
        print(f"\n--- Syncing branch: {branch} ---")

    # Get the default branch to determine if this is it
    default_branch = git.get_default_branch()
    is_default = (branch == default_branch)

    # Ensure branch record exists
    bq.ensure_branch(client, dataset, repo_id, branch, is_default=is_default)

    # Get last processed commit for this branch
    last_sha = bq.get_branch_head_sha(client, dataset, repo_id, branch)

    if verbose:
        if last_sha:
            print(f"Last processed commit for {branch}: {last_sha[:8]}")
        else:
            print(f"No previous commits found for {branch}, processing full history")

    # Get new commits for this branch
    commits = git.get_new_commits(last_sha, branch)

    if verbose:
        print(f"Found {len(commits)} new commits to process for {branch}")

    # Process commits
    commits_processed = 0
    file_changes_processed = 0
    pending_commits: list[dict] = []
    pending_changes: list[dict] = []

    def flush() -> None:
        """Write the pending batch, then advance the branch head past it."""
        if not pending_commits:
            return

        bq.insert_commits(client, dataset, pending_commits)
        bq.insert_file_changes(client, dataset, pending_changes)

        # Only once the rows are durable, so a failed run resumes from here
        # rather than duplicating what it already wrote.
        bq.update_branch_head(
            client, dataset, repo_id, branch, pending_commits[-1]["sha"]
        )

        pending_commits.clear()
        pending_changes.clear()

    for commit in commits:
        # Add repo_id and branch to commit
        commit["repo_id"] = repo_id
        commit["branch"] = branch

        if verbose:
            msg_preview = commit["message"][:50].replace("\n", " ")
            print(f"  Processing commit {commit['sha'][:8]}: {msg_preview}...")

        # Get file changes for this commit
        changes = git.get_file_changes(
            commit["sha"], commit.get("parent_sha"), max_diff_size
        )

        # Add repo_id and commit_sha to each change
        for change in changes:
            change["repo_id"] = repo_id
            change["commit_sha"] = commit["sha"]

        pending_commits.append(commit)
        pending_changes.extend(changes)
        commits_processed += 1
        file_changes_processed += len(changes)

        if len(pending_commits) >= COMMIT_BATCH_SIZE:
            flush()

    flush()

    if not commits_processed and not last_sha:
        # No commits and no previous head - get the current branch head
        current_head = git.get_branch_head_sha(branch)
        if current_head:
            bq.update_branch_head(client, dataset, repo_id, branch, current_head)

    # Update current files for this branch
    if verbose:
        print(f"Updating current files for {branch}...")

    current_files = git.get_current_files(max_file_size, branch)
    bq.upsert_current_files(client, dataset, repo_id, current_files, branch)

    if verbose:
        print(f"Updated {len(current_files)} current files for {branch}")

    return {
        "branch": branch,
        "commits_processed": commits_processed,
        "file_changes_processed": file_changes_processed,
        "current_files_updated": len(current_files),
    }


def init(
    project: str,
    dataset: str,
    location: str | None = None,
    verbose: bool = False,
) -> str:
    """Initialize BigQuery schema.

    Args:
        project: GCP project ID
        dataset: BigQuery dataset name
        location: BigQuery location to create the dataset in (e.g. "EU").
            Defaults to the location of an existing dataset, else "US".
        verbose: Print progress information

    Returns:
        The location the dataset lives in.
    """
    if verbose:
        print(f"Initializing schema in {project}.{dataset}")

    _, resolved_location = connect(project, dataset, location)

    if verbose:
        print(f"Schema initialized successfully in location {resolved_location}!")
        print("Tables created:")
        for table_name in bq.TABLE_SCHEMAS:
            print(f"  - {table_name}")

    return resolved_location
