"""Tests for git helpers that need no repository."""

from gtl.git import truncate_diff


class TestTruncateDiff:
    def test_leaves_a_diff_under_the_limit_untouched(self):
        diff = "+one line\n"
        assert truncate_diff(diff, 1024) == diff

    def test_zero_disables_truncation(self):
        diff = "x" * 5000
        assert truncate_diff(diff, 0) == diff

    def test_negative_disables_truncation(self):
        diff = "x" * 5000
        assert truncate_diff(diff, -1) == diff

    def test_truncates_and_marks_an_oversized_diff(self):
        result = truncate_diff("x" * 5000, 100)

        assert result.startswith("x" * 100)
        assert "[gtl: diff truncated at 100 bytes]" in result

    def test_measures_bytes_not_characters(self):
        # 3 bytes per character, so 10 characters exceed a 12 byte budget
        diff = "é" * 10
        assert truncate_diff(diff, 30) == diff
        assert truncate_diff(diff, 12).startswith("é" * 6)

    def test_never_emits_a_broken_multibyte_character(self):
        # A 5 byte cut lands mid-character; the partial byte must be dropped
        result = truncate_diff("é" * 10, 5)

        result.encode("utf-8").decode("utf-8")  # raises if malformed
        assert result.startswith("é" * 2)


class TestGetBranchHeadSha:
    """Regression: `git rev-parse <missing>` echoes its argument to stdout."""

    def test_returns_none_for_an_unknown_branch(self, tmp_path, monkeypatch):
        import subprocess

        from gtl.git import get_branch_head_sha

        repo = tmp_path / "repo"
        repo.mkdir()
        for args in (
            ["init", "-b", "master"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "T"],
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        (repo / "f.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "c"], check=True, capture_output=True)
        monkeypatch.chdir(repo)

        assert get_branch_head_sha("no-such-branch") is None
        head = get_branch_head_sha("master")
        assert head is not None and len(head) == 40
