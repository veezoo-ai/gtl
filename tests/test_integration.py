"""End-to-end sync against a real git repository and a fake BigQuery."""

import subprocess

import pytest

from tests.fake_bigquery import FakeClient
from gtl import sync as sync_mod

DATASET = "git_repo"
REPO_ID = "github.com/example-org/example-repo"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A repository with three commits: add two files, modify one, delete one."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "remote", "add", "origin", f"https://{REPO_ID}.git")

    (path / "README.md").write_text("# hello\n")
    (path / "doomed.txt").write_text("temporary\n")
    git(path, "add", "-A")
    git(path, "commit", "-m", "initial commit")

    (path / "README.md").write_text("# hello\n\nmore words\n")
    git(path, "add", "-A")
    git(path, "commit", "-m", "expand readme")

    (path / "doomed.txt").unlink()
    git(path, "add", "-A")
    git(path, "commit", "-m", "remove doomed file")

    return path


@pytest.fixture
def client(monkeypatch):
    """A FakeClient that gtl's connect() will hand back."""
    fake = FakeClient(project="proj", location="EU")
    monkeypatch.setattr(sync_mod.bq, "get_client", lambda project, location=None: fake)
    return fake


def run_sync(**kwargs):
    return sync_mod.sync(
        project="proj", dataset=DATASET, location="EU", repo_id=REPO_ID, **kwargs
    )


def rows(client, table):
    return client.tables[f"proj.{DATASET}.{table}"]


class TestFullSync:
    def test_syncs_a_whole_repository(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        result = run_sync(branch="main")

        assert result["commits_processed"] == 3
        assert result["location"] == "EU"

        commits = rows(client, "commits")
        assert [c["message"] for c in commits] == [
            "initial commit",
            "expand readme",
            "remove doomed file",
        ]
        assert all(c["repo_id"] == REPO_ID for c in commits)
        assert all(c["branch"] == "main" for c in commits)

    def test_creates_the_dataset_in_the_requested_location(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        assert client.datasets[DATASET] == "EU"

    def test_records_file_changes_including_the_deletion(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        changes = rows(client, "file_changes")
        by_type = {(c["file_path"], c["change_type"]) for c in changes}
        assert ("README.md", "A") in by_type
        assert ("doomed.txt", "A") in by_type
        assert ("README.md", "M") in by_type
        assert ("doomed.txt", "D") in by_type
        assert all(c["diff"] for c in changes)

    def test_current_files_reflect_the_final_tree(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        current = rows(client, "current_files")
        # doomed.txt was deleted, so it must not survive the MERGE
        assert {f["file_path"] for f in current} == {"README.md"}
        assert current[0]["content"] == "# hello\n\nmore words\n"
        assert current[0]["branch"] == "main"

    def test_branch_head_matches_the_last_commit(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "main"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        branches = rows(client, "branches")
        assert len(branches) == 1
        assert branches[0]["head_sha"] == head
        assert branches[0]["is_default"] is True

    def test_registers_the_repository_once(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)

        run_sync(branch="main")
        run_sync(branch="main")

        assert [r["id"] for r in rows(client, "repositories")] == [REPO_ID]


class TestContentFidelity:
    """Regressions: content used to be routed through run_git, which strips."""

    def test_preserves_trailing_and_leading_whitespace(self, repo, client, monkeypatch):
        (repo / "spaced.md").write_text("\n  indented\n\n\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add spaced file")
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        stored = {f["file_path"]: f for f in rows(client, "current_files")}
        assert stored["spaced.md"]["content"] == "\n  indented\n\n\n"
        assert stored["spaced.md"]["size_bytes"] == len("\n  indented\n\n\n")

    def test_skips_binary_files_instead_of_crashing(self, repo, client, monkeypatch):
        # Invalid UTF-8: decoding this as text used to raise before is_binary ran
        (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add binary")
        monkeypatch.chdir(repo)

        result = run_sync(branch="main")

        assert result["commits_processed"] == 4
        assert "logo.png" not in {f["file_path"] for f in rows(client, "current_files")}

    def test_reports_size_in_bytes_not_characters(self, repo, client, monkeypatch):
        (repo / "unicode.md").write_text("héllo\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add unicode")
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        stored = {f["file_path"]: f for f in rows(client, "current_files")}
        assert stored["unicode.md"]["content"] == "héllo\n"
        assert stored["unicode.md"]["size_bytes"] == 7  # 6 characters, 7 bytes


class TestNonAsciiPaths:
    """Regression: git quotes non-ASCII paths unless core.quotepath is off."""

    def test_keeps_files_whose_path_has_a_curly_apostrophe(
        self, repo, client, monkeypatch
    ):
        # Exactly the shape that appears in scraped web content
        name = "it\u2019s-time.md"
        (repo / name).write_text("body\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add curly apostrophe file")
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        stored = {f["file_path"]: f for f in rows(client, "current_files")}
        # Quoted output would drop the file entirely and escape the path
        assert name in stored
        assert stored[name]["content"] == "body\n"
        assert not any(p.startswith('"') for p in stored)

    def test_records_accented_paths_unescaped_in_file_changes(
        self, repo, client, monkeypatch
    ):
        (repo / "caf\u00e9.md").write_text("espresso\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add accented file")
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        paths = {c["file_path"] for c in rows(client, "file_changes")}
        assert "caf\u00e9.md" in paths
        assert not any("\\3" in p for p in paths), "octal escapes leaked into paths"


class TestInitialCommit:
    """Regression: `git diff --root` compared against the working tree."""

    def test_initial_commit_records_additions_against_the_empty_tree(
        self, repo, client, monkeypatch
    ):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        root_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--max-parents=0", "main"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        initial = [c for c in rows(client, "file_changes") if c["commit_sha"] == root_sha]

        # Both files are additions; neither is a modification or a deletion
        assert {(c["file_path"], c["change_type"]) for c in initial} == {
            ("README.md", "A"),
            ("doomed.txt", "A"),
        }
        assert all(c["deletions"] == 0 for c in initial)
        assert all("new file" in c["diff"] for c in initial)


class TestIncrementalSync:
    def test_second_sync_is_a_no_op(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)
        run_sync(branch="main")

        result = run_sync(branch="main")

        assert result["commits_processed"] == 0
        assert len(rows(client, "commits")) == 3

    def test_only_new_commits_are_written(self, repo, client, monkeypatch):
        monkeypatch.chdir(repo)
        run_sync(branch="main")

        (repo / "NEW.md").write_text("fresh\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add new file")

        result = run_sync(branch="main")

        assert result["commits_processed"] == 1
        assert len(rows(client, "commits")) == 4
        assert {f["file_path"] for f in rows(client, "current_files")} == {
            "README.md",
            "NEW.md",
        }


class TestBatching:
    def test_head_advances_per_batch_so_an_interrupted_sync_resumes(
        self, repo, client, monkeypatch
    ):
        # One commit per batch: the head must move after each, not just at the end
        monkeypatch.setattr(sync_mod, "COMMIT_BATCH_SIZE", 1)
        monkeypatch.chdir(repo)

        heads = []
        real_update = sync_mod.bq.update_branch_head

        def spy(client_, dataset, repo_id, branch, head_sha):
            heads.append(head_sha)
            return real_update(client_, dataset, repo_id, branch, head_sha)

        monkeypatch.setattr(sync_mod.bq, "update_branch_head", spy)

        run_sync(branch="main")

        assert len(heads) == 3
        assert len(rows(client, "commits")) == 3

    def test_a_single_batch_writes_one_load_job_per_table(
        self, repo, client, monkeypatch
    ):
        monkeypatch.chdir(repo)

        run_sync(branch="main")

        commit_loads = [t for t, _ in client.load_jobs if t.endswith(".commits")]
        assert len(commit_loads) == 1, "3 commits should batch into one load job"
