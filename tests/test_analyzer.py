from __future__ import annotations
import json
from unittest.mock import MagicMock, patch
from pr_pilot.analyzer import (
    describe_pr, suggest_labels, review_pr, PRDescription,
    review_pr_as_comment, generate_changelog, ChangelogEntry,
)
from pr_pilot.templates import REVIEW_COMMENT_HEADER


def _mock_openai_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


_SAMPLE_DESC = {
    "title": "Add dark mode support",
    "summary": "Implements a dark mode toggle that persists via localStorage.",
    "changes": ["Add ThemeToggle component", "Add useTheme hook", "Update global CSS variables"],
    "breaking": False,
    "breaking_notes": None,
    "test_plan": "Toggle dark mode, reload page — preference should persist.",
    "labels": ["feature"],
}


@patch("pr_pilot.analyzer.get_diff", return_value="diff --git a/theme.css ...")
@patch("pr_pilot.analyzer.get_commits", return_value="abc123 Add dark mode")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/dark-mode")
def test_describe_pr_basic(mock_branch, mock_commits, mock_diff):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(_SAMPLE_DESC))
        desc = describe_pr("fake-key", base="main")
    assert desc.title == "Add dark mode support"
    assert desc.breaking is False
    assert "feature" in desc.labels
    assert len(desc.changes) == 3


@patch("pr_pilot.analyzer.get_diff", return_value="diff --git a/theme.css ...")
@patch("pr_pilot.analyzer.get_commits", return_value="abc123 Add dark mode")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/dark-mode")
def test_describe_pr_strips_markdown_fence(mock_branch, mock_commits, mock_diff):
    raw = f"```json\n{json.dumps(_SAMPLE_DESC)}\n```"
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(raw)
        desc = describe_pr("fake-key")
    assert desc.title == "Add dark mode support"


def test_describe_pr_to_markdown():
    desc = PRDescription(**_SAMPLE_DESC)
    md = desc.to_markdown()
    assert "## Summary" in md
    assert "## Changes" in md
    assert "## Test Plan" in md
    assert "ThemeToggle" in md


def test_suggest_labels():
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response('["bug", "security"]')
        labels = suggest_labels("fake-key", "Fix XSS in comment input", "sanitize user input")
    assert "bug" in labels
    assert "security" in labels


@patch("pr_pilot.analyzer.get_diff", return_value="- old line\n+ new line")
@patch("pr_pilot.analyzer.get_commits", return_value="")
@patch("pr_pilot.analyzer.get_branch", return_value="fix/xss")
def test_review_pr(mock_branch, mock_commits, mock_diff):
    review_text = "This PR sanitizes user input to prevent XSS attacks."
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(review_text)
        result = review_pr("fake-key")
    assert "XSS" in result


# ── Auto-comment tests ────────────────────────────────────────────────────────

@patch("pr_pilot.analyzer.get_diff", return_value="+ new feature code")
@patch("pr_pilot.analyzer.get_commits", return_value="")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/x")
def test_review_pr_as_comment_contains_marker(mock_branch, mock_commits, mock_diff):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("Looks good overall.")
        comment = review_pr_as_comment("fake-key")
    assert REVIEW_COMMENT_HEADER in comment
    assert "pr-pilot review" in comment
    assert "Looks good overall." in comment


@patch("pr_pilot.analyzer.get_diff", return_value="")
@patch("pr_pilot.analyzer.get_commits", return_value="")
@patch("pr_pilot.analyzer.get_branch", return_value="main")
def test_review_pr_as_comment_no_diff(mock_branch, mock_commits, mock_diff):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("No changes to review.")
        comment = review_pr_as_comment("fake-key")
    assert "No changes to review." in comment


# ── Changelog tests ───────────────────────────────────────────────────────────

_SAMPLE_CHANGELOG = {
    "version": "minor",
    "highlights": "Adds dark mode and fixes login bug.",
    "added": ["Dark mode toggle with localStorage persistence"],
    "changed": ["Login flow now uses refresh tokens"],
    "fixed": ["Session expiry on mobile Safari"],
    "removed": [],
    "security": [],
}


@patch("pr_pilot.analyzer.get_diff", return_value="diff --git a/theme.css ...")
@patch("pr_pilot.analyzer.get_commits", return_value="")
@patch("pr_pilot.analyzer.get_branch", return_value="main")
@patch("pr_pilot.analyzer._get_commits_since_tag", return_value="abc123 Add dark mode\ndef456 Fix login")
@patch("pr_pilot.analyzer._get_current_version", return_value="1.2.3")
def test_generate_changelog(mock_ver, mock_commits_tag, mock_branch, mock_commits, mock_diff):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(_SAMPLE_CHANGELOG))
        entry, new_version = generate_changelog("fake-key")
    assert entry.version_bump == "minor"
    assert new_version == "1.3.0"
    assert "Dark mode" in entry.added[0]
    assert entry.security == []


def test_changelog_entry_to_markdown():
    entry = ChangelogEntry(
        version_bump="patch",
        highlights="Bug fixes.",
        added=[],
        changed=[],
        fixed=["Fix crash on startup"],
        removed=[],
        security=[],
    )
    md = entry.to_markdown("1.0.1", "2026-06-02")
    assert "## [1.0.1] - 2026-06-02" in md
    assert "### Fixed" in md
    assert "Fix crash on startup" in md
    assert "### Added" not in md   # empty section should be omitted


def test_version_bump():
    from pr_pilot.analyzer import _bump_version
    assert _bump_version("1.2.3", "patch") == "1.2.4"
    assert _bump_version("1.2.3", "minor") == "1.3.0"
    assert _bump_version("1.2.3", "major") == "2.0.0"
