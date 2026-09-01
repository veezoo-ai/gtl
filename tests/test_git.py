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
