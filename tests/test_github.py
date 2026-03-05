import pytest

from takopi.github import (
    GitHubError,
    GitHubIssue,
    _parse_owner_repo,
    issue_branch_name,
)


class TestParseOwnerRepo:
    def test_https_url(self) -> None:
        assert _parse_owner_repo("https://github.com/banteg/takopi.git") == (
            "banteg",
            "takopi",
        )

    def test_https_url_no_suffix(self) -> None:
        assert _parse_owner_repo("https://github.com/banteg/takopi") == (
            "banteg",
            "takopi",
        )

    def test_ssh_url(self) -> None:
        assert _parse_owner_repo("git@github.com:banteg/takopi.git") == (
            "banteg",
            "takopi",
        )

    def test_ssh_url_no_suffix(self) -> None:
        assert _parse_owner_repo("git@github.com:banteg/takopi") == (
            "banteg",
            "takopi",
        )

    def test_invalid_url(self) -> None:
        with pytest.raises(GitHubError, match="cannot parse"):
            _parse_owner_repo("https://gitlab.com/foo/bar")

    def test_too_few_segments(self) -> None:
        with pytest.raises(GitHubError, match="cannot parse owner/repo"):
            _parse_owner_repo("git@github.com:onlyone")


class TestIssueBranchName:
    def test_basic(self) -> None:
        assert issue_branch_name(42) == "issue/42"

    def test_large_number(self) -> None:
        assert issue_branch_name(9999) == "issue/9999"


class TestGitHubIssue:
    def test_frozen(self) -> None:
        issue = GitHubIssue(
            number=1,
            title="test",
            labels=("bug",),
            state="open",
            html_url="https://github.com/a/b/issues/1",
        )
        with pytest.raises(AttributeError):
            issue.number = 2  # type: ignore[misc]
