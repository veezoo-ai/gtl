"""Git operations for gtl."""

import subprocess
import re
from pathlib import Path

# Diffing the initial commit means diffing against the empty tree, which
# `git diff` cannot do -- it would compare against the working tree instead.
ROOT_DIFF = ["diff-tree", "--root", "--no-commit-id", "-M"]

# core.quotepath defaults to true, which makes git wrap any path containing
# non-ASCII bytes in quotes and escape them octally ("caf\303\251.md"). Feeding
# that back to `git show` fails, so such files were silently dropped from
# current_files and recorded under mangled paths in file_changes. Scraped web
# content is full of curly apostrophes, so this is not a rare edge case.
GIT = ["git", "-c", "core.quotepath=false"]


def run_git(*args: str, check: bool = True) -> str:
    """Run a git command and return stripped stdout.

    Suitable for metadata (SHAs, branch names, porcelain output). File contents
    must use run_git_bytes instead, which neither strips nor decodes.
    """
    result = subprocess.run(
        [*GIT, *args],
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def run_git_bytes(*args: str) -> bytes | None:
    """Run a git command and return raw stdout, or None if it failed.

    File contents go through here rather than run_git: stripping would drop
    trailing newlines and leading whitespace, and decoding as text raises on
    binary files before is_binary ever gets to reject them.
    """
    result = subprocess.run([*GIT, *args], capture_output=True)
    if result.returncode != 0:
        return None
    return result.stdout


def get_current_branch() -> str | None:
    """Get the currently checked out branch name.

    Returns None if in detached HEAD state or if git command fails.
    """
    try:
        branch = run_git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        if branch and branch != "HEAD":
            return branch
        return None
    except subprocess.CalledProcessError:
        return None


def get_branches(remote: bool = False) -> list[str]:
    """Get list of branches.

    Args:
        remote: If True, list remote branches (origin/*). If False, list local branches.

    Returns:
        List of branch names (without 'origin/' prefix for remote branches).
    """
    try:
        if remote:
            output = run_git("branch", "-r", "--format=%(refname:short)", check=False)
        else:
            output = run_git("branch", "--format=%(refname:short)", check=False)
    except subprocess.CalledProcessError:
        return []

    if not output:
        return []

    branches = []
    for line in output.splitlines():
        branch = line.strip()
        if not branch:
            continue
        # Skip HEAD pointer for remote branches
        if branch.endswith("/HEAD"):
            continue
        # Remove 'origin/' prefix for remote branches
        if remote and branch.startswith("origin/"):
            branch = branch[7:]
        branches.append(branch)

    return branches


def get_default_branch() -> str:
    """Get the default branch name (main or master).

    Checks for existence of main first, then master.
    Returns 'main' if neither exists.
    """
    # Check local branches first
    local_branches = get_branches(remote=False)
    if "main" in local_branches:
        return "main"
    if "master" in local_branches:
        return "master"

    # Check remote branches
    remote_branches = get_branches(remote=True)
    if "main" in remote_branches:
        return "main"
    if "master" in remote_branches:
        return "master"

    # Default to main
    return "main"


def get_branch_head_sha(branch: str) -> str | None:
    """Get the HEAD SHA of a branch.

    Args:
        branch: Branch name

    Returns:
        The SHA of the branch HEAD, or None if branch doesn't exist.
    """
    try:
        # --verify --quiet prints nothing and exits non-zero for an unknown
        # ref. Plain `git rev-parse <branch>` echoes the argument back on
        # stdout, so a missing branch would be recorded as a head_sha of
        # literally "main".
        sha = run_git(
            "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}", check=False
        )
        return sha or None
    except subprocess.CalledProcessError:
        return None


def is_ancestor(ancestor_sha: str, descendant_sha: str) -> bool:
    """Check if ancestor_sha is an ancestor of descendant_sha.

    Args:
        ancestor_sha: The potential ancestor commit SHA
        descendant_sha: The potential descendant commit SHA

    Returns:
        True if ancestor_sha is an ancestor of descendant_sha.
    """
    try:
        result = subprocess.run(
            [*GIT, "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


def get_repo_id() -> str | None:
    """Auto-detect repo ID from git remote origin URL.

    Converts URLs like:
    - https://github.com/org/repo.git -> github.com/org/repo
    - git@github.com:org/repo.git -> github.com/org/repo

    Returns None if no remote origin is configured.
    """
    url = run_git("remote", "get-url", "origin", check=False)
    if not url:
        return None

    # Handle SSH URLs (git@github.com:org/repo.git)
    ssh_match = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host, path = ssh_match.groups()
        return f"{host}/{path}"

    # Handle HTTPS URLs (https://github.com/org/repo.git)
    https_match = re.match(r"https?://([^/]+)/(.+?)(?:\.git)?$", url)
    if https_match:
        host, path = https_match.groups()
        return f"{host}/{path}"

    # Fallback: return URL as-is
    return url


def get_repo_name(repo_id: str | None = None) -> str | None:
    """Get the repository name from the remote URL or repo_id."""
    if repo_id is None:
        repo_id = get_repo_id()
    if repo_id is None:
        return None
    return repo_id.split("/")[-1]


def get_repo_url() -> str | None:
    """Get the repository URL."""
    return run_git("remote", "get-url", "origin", check=False) or None


def get_new_commits(last_sha: str | None, branch: str | None = None) -> list[dict]:
    """Get commits since last_sha with metadata.

    Args:
        last_sha: The SHA of the last processed commit. If None, returns all commits.
        branch: The branch to get commits from. If None, uses HEAD.

    Returns:
        Commits in oldest-first order for proper insertion.
    """
    # Determine the target ref
    target_ref = branch if branch else "HEAD"

    # Build the revision range
    if last_sha:
        # Check if last_sha is an ancestor of target_ref
        if not is_ancestor(last_sha, target_ref):
            # last_sha is not in this branch's history, start from the beginning
            rev_range = target_ref
        else:
            # Get commits after last_sha up to target_ref
            rev_range = f"{last_sha}..{target_ref}"
    else:
        # Get all commits
        rev_range = target_ref

    # Format: sha|parent_sha|author_name|author_email|timestamp|message
    # Use %x00 as delimiter to handle special chars in messages
    format_str = "%H%x00%P%x00%an%x00%ae%x00%aI%x00%B%x00%x01"

    try:
        output = run_git(
            "log",
            "--reverse",  # Oldest first
            "--first-parent",  # Only follow first parent
            f"--format={format_str}",
            rev_range,
        )
    except subprocess.CalledProcessError:
        return []

    if not output:
        return []

    commits = []
    for entry in output.split("\x01"):
        entry = entry.strip()
        if not entry:
            continue

        parts = entry.split("\x00")
        if len(parts) < 6:
            continue

        sha, parents, author_name, author_email, timestamp, message = parts[:6]

        # Get first parent (or None for initial commit)
        parent_sha = parents.split()[0] if parents else None

        commits.append({
            "sha": sha,
            "parent_sha": parent_sha,
            "author_name": author_name,
            "author_email": author_email,
            "committed_at": timestamp,
            "message": message.strip(),
        })

    return commits


def get_file_changes(
    sha: str,
    parent_sha: str | None,
    max_diff_size: int = 0,
) -> list[dict]:
    """Get per-file diffs for a commit.

    Args:
        sha: Commit SHA.
        parent_sha: Parent commit SHA, or None for the initial commit.
        max_diff_size: Truncate each diff to this many bytes. 0 disables it.

    Returns list of dicts with:
    - file_path, change_type, old_path, diff, additions, deletions
    """
    changes = []

    # Get the list of changed files with stats
    if parent_sha:
        diff_args = ["diff", "--numstat", "-M", parent_sha, sha]
    else:
        # Initial commit: compare against the empty tree. `git diff --root <sha>`
        # would compare it against the working tree instead.
        diff_args = ROOT_DIFF + ["--numstat", sha]

    numstat_output = run_git(*diff_args, check=False)

    # Get name-status for change types
    if parent_sha:
        status_args = ["diff", "--name-status", "-M", parent_sha, sha]
    else:
        status_args = ROOT_DIFF + ["--name-status", sha]

    status_output = run_git(*status_args, check=False)

    # Parse name-status to get change types and paths
    file_info = {}
    for line in status_output.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue

        status = parts[0]
        change_type = status[0]  # A, M, D, or R (with percentage for renames)

        if change_type == "R" and len(parts) >= 3:
            old_path = parts[1]
            file_path = parts[2]
            file_info[file_path] = {"change_type": "R", "old_path": old_path}
        else:
            file_path = parts[1]
            file_info[file_path] = {"change_type": change_type, "old_path": None}

    # Parse numstat for additions/deletions
    for line in numstat_output.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue

        additions_str, deletions_str, path = parts[0], parts[1], parts[2]

        # Handle renamed files (path shows as old_path => new_path)
        if " => " in path:
            # Could be in format: dir/{old => new}/file or old => new
            old_path, new_path = None, path
            if "{" in path:
                # Complex rename pattern
                match = re.match(r"(.*)?\{(.*) => (.*)\}(.*)?", path)
                if match:
                    prefix, old_part, new_part, suffix = match.groups()
                    prefix = prefix or ""
                    suffix = suffix or ""
                    new_path = f"{prefix}{new_part}{suffix}"
                    old_path = f"{prefix}{old_part}{suffix}"
            else:
                parts = path.split(" => ")
                if len(parts) == 2:
                    old_path, new_path = parts
            path = new_path

        # Binary files show as "-" for additions/deletions
        if additions_str == "-" or deletions_str == "-":
            continue  # Skip binary files

        additions = int(additions_str)
        deletions = int(deletions_str)

        info = file_info.get(path, {"change_type": "M", "old_path": None})

        # Get the actual diff for this file
        diff = get_file_diff(
            sha, parent_sha, path, info.get("old_path"), max_diff_size
        )

        changes.append({
            "file_path": path,
            "change_type": info["change_type"],
            "old_path": info["old_path"],
            "diff": diff,
            "additions": additions,
            "deletions": deletions,
        })

    return changes


def truncate_diff(diff: str, max_diff_size: int) -> str:
    """Truncate a diff that exceeds max_diff_size bytes.

    A value of 0 or less disables truncation. A single BigQuery row is capped
    well below the size a wholesale file rewrite can produce, so an uncapped
    diff can fail the whole load job. The marker keeps truncated rows obvious
    in BigQuery rather than silently short.
    """
    if max_diff_size <= 0:
        return diff

    encoded = diff.encode("utf-8")
    if len(encoded) <= max_diff_size:
        return diff

    # errors="ignore" drops a trailing partial multi-byte character
    truncated = encoded[:max_diff_size].decode("utf-8", errors="ignore")
    return f"{truncated}\n... [gtl: diff truncated at {max_diff_size} bytes]"


def get_file_diff(
    sha: str,
    parent_sha: str | None,
    file_path: str,
    old_path: str | None,
    max_diff_size: int = 0,
) -> str:
    """Get the diff for a specific file in a commit."""
    if parent_sha:
        if old_path:
            diff_args = ["diff", parent_sha, sha, "--", old_path, file_path]
        else:
            diff_args = ["diff", parent_sha, sha, "--", file_path]
    else:
        diff_args = ROOT_DIFF + ["-p", sha, "--", file_path]

    return truncate_diff(run_git(*diff_args, check=False), max_diff_size)


def get_current_files(max_size: int, branch: str | None = None) -> list[dict]:
    """Get all current text files in repo with contents.

    Args:
        max_size: Maximum file size in bytes to include.
        branch: Optional branch name to get files from. If None, uses working tree.

    Returns:
        List of dicts with file_path, content, size_bytes, last_commit_sha.
    """
    files = []

    if branch:
        # Get files from a specific branch using git ls-tree
        return get_files_from_branch(max_size, branch)

    # Get list of all tracked files from working tree
    output = run_git("ls-files")
    if not output:
        return files

    for file_path in output.splitlines():
        if not file_path:
            continue

        path = Path(file_path)
        if not path.exists():
            continue

        # Check file size
        size_bytes = path.stat().st_size
        if size_bytes > max_size:
            continue

        # Read file content
        try:
            content = path.read_bytes()
        except (OSError, IOError):
            continue

        # Skip binary files
        if is_binary(content):
            continue

        # Decode content
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            continue

        # Get last commit that touched this file
        last_commit_sha = run_git("log", "-1", "--format=%H", "--", file_path, check=False)

        files.append({
            "file_path": file_path,
            "content": content_str,
            "size_bytes": size_bytes,
            "last_commit_sha": last_commit_sha or None,
        })

    return files


def get_files_from_branch(max_size: int, branch: str) -> list[dict]:
    """Get all text files from a specific branch.

    Args:
        max_size: Maximum file size in bytes to include.
        branch: Branch name to get files from.

    Returns:
        List of dicts with file_path, content, size_bytes, last_commit_sha.
    """
    files = []

    # Get list of all files in the branch
    try:
        output = run_git("ls-tree", "-r", "--name-only", branch)
    except subprocess.CalledProcessError:
        return files

    if not output:
        return files

    for file_path in output.splitlines():
        if not file_path:
            continue

        # Get file content from the branch, as raw bytes
        content_bytes = run_git_bytes("show", f"{branch}:{file_path}")
        if content_bytes is None:
            continue

        # Get file size
        size_bytes = len(content_bytes)

        if size_bytes > max_size:
            continue

        # Skip binary files
        if is_binary(content_bytes):
            continue

        # Decode content
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue

        # Get last commit that touched this file on this branch
        last_commit_sha = run_git(
            "log", "-1", "--format=%H", branch, "--", file_path, check=False
        )

        files.append({
            "file_path": file_path,
            "content": content,
            "size_bytes": size_bytes,
            "last_commit_sha": last_commit_sha or None,
        })

    return files


def is_binary(data: bytes) -> bool:
    """Check if content is binary (has null bytes in first 8KB)."""
    # Check first 8KB for null bytes
    chunk = data[:8192]
    return b"\x00" in chunk
