from __future__ import annotations
import json
from unittest.mock import MagicMock, patch
from pr_pilot.analyzer import describe_pr, suggest_labels, review_pr, PRDescription


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
