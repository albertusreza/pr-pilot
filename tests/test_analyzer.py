from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock, patch
from pr_pilot.analyzer import (
    describe_pr, suggest_labels, review_pr, PRDescription,
    review_pr_as_comment, generate_changelog, ChangelogEntry,
    suggest_reviewers, generate_standup, create_issues_from_todos, _bump_version,
    generate_commit_message, CommitMessage, run_release,
    generate_docstrings, suggest_branch, explain_code,
    _extract_functions, _detect_language,
    generate_tests, GeneratedTests,
    scan_security, SecurityReport, SecurityIssue,
    get_diff, get_diff_stat, _find_pr_template, _parse_json_response,
)
from pr_pilot.templates import (
    REVIEW_COMMENT_HEADER, REVIEWER_COMMENT_HEADER, SECURITY_COMMENT_HEADER,
    DESCRIBE_TEMPLATE_SYSTEM,
)


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


@patch("pr_pilot.analyzer._find_pr_template", return_value=None)
@patch("pr_pilot.analyzer.get_diff_stat", return_value=" theme.css | 10 ++")
@patch("pr_pilot.analyzer.get_diff", return_value="diff --git a/theme.css ...")
@patch("pr_pilot.analyzer.get_commits", return_value="abc123 Add dark mode")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/dark-mode")
def test_describe_pr_basic(mock_branch, mock_commits, mock_diff, mock_stat, mock_tmpl):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(_SAMPLE_DESC))
        desc = describe_pr("fake-key", base="main")
    assert desc.title == "Add dark mode support"
    assert desc.breaking is False
    assert "feature" in desc.labels
    assert len(desc.changes) == 3


@patch("pr_pilot.analyzer._find_pr_template", return_value=None)
@patch("pr_pilot.analyzer.get_diff_stat", return_value=" theme.css | 10 ++")
@patch("pr_pilot.analyzer.get_diff", return_value="diff --git a/theme.css ...")
@patch("pr_pilot.analyzer.get_commits", return_value="abc123 Add dark mode")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/dark-mode")
def test_describe_pr_strips_markdown_fence(mock_branch, mock_commits, mock_diff, mock_stat, mock_tmpl):
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


def test_describe_pr_to_markdown_uses_raw_body():
    """When _raw_body is set (template mode), to_markdown returns it verbatim."""
    desc = PRDescription(**{**_SAMPLE_DESC, "_raw_body": "## My Template\nFilled in."})
    assert desc.to_markdown() == "## My Template\nFilled in."


# ── Smart diff tests ──────────────────────────────────────────────────────────

def test_get_diff_short_returns_as_is():
    with patch("pr_pilot.analyzer._git", return_value="small diff"):
        result = get_diff("main")
    assert result == "small diff"


def test_get_diff_large_summarises_by_file():
    big_diff = "x" * 30_000
    file_list = "auth.py\nutils.py"
    file_diffs = {"auth.py": "a" * 500, "utils.py": "b" * 500}

    def fake_git(*args):
        if "--name-only" in args:
            return file_list
        if "--" in args:
            fname = args[-1]
            return file_diffs.get(fname, "")
        return big_diff

    with patch("pr_pilot.analyzer._git", side_effect=fake_git):
        result = get_diff("main")
    assert "aaa" in result
    assert "bbb" in result
    # Should NOT be the raw big_diff
    assert len(result) < 30_000


# ── PR template detection tests ───────────────────────────────────────────────

def test_find_pr_template_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _find_pr_template() is None


def test_find_pr_template_detects_github_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "pull_request_template.md").write_text("## Summary\n\n## Changes\n")
    result = _find_pr_template()
    assert result is not None
    assert "## Summary" in result


@patch("pr_pilot.analyzer._find_pr_template",
       return_value="## Summary\n\n## Changes\n\n## Test Plan\n")
@patch("pr_pilot.analyzer.get_diff_stat", return_value=" auth.py | 5 +")
@patch("pr_pilot.analyzer.get_diff", return_value="+ def login(): pass")
@patch("pr_pilot.analyzer.get_commits", return_value="abc Add login")
@patch("pr_pilot.analyzer.get_branch", return_value="feat/login")
def test_describe_pr_uses_template(mock_branch, mock_commits, mock_diff, mock_stat, mock_tmpl):
    payload = {
        "title": "Add login endpoint",
        "body": "## Summary\nAdds a login endpoint.\n\n## Changes\n- Add login func\n\n## Test Plan\nRun tests.",
        "labels": ["feature"],
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        desc = describe_pr("fake-key", use_template=True)
    assert desc.title == "Add login endpoint"
    assert desc._raw_body is not None
    assert "## Summary" in desc.to_markdown()
    # Verify TEMPLATE system prompt was used
    call_args = MockOpenAI.return_value.chat.completions.create.call_args
    system_msg = call_args[1]["messages"][0]["content"]
    assert "template" in system_msg.lower()


# ── _parse_json_response tests ────────────────────────────────────────────────

def test_parse_json_response_plain():
    assert _parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_fenced():
    assert _parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_response_fenced_no_lang():
    assert _parse_json_response('```\n{"a": 1}\n```') == {"a": 1}


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
    assert _bump_version("1.2.3", "patch") == "1.2.4"
    assert _bump_version("1.2.3", "minor") == "1.3.0"
    assert _bump_version("1.2.3", "major") == "2.0.0"


# ── Reviewer suggester tests ──────────────────────────────────────────────────

@patch("pr_pilot.analyzer._get_blame_summary", return_value=("alice", "  src/auth.py: bob, carol\n  src/api.py: bob"))
def test_suggest_reviewers_basic(mock_blame):
    payload = {"reviewers": ["bob", "carol"], "reasoning": "They own auth.py and api.py"}
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        result = suggest_reviewers("fake-key")
    assert "bob" in result.reviewers
    assert "carol" in result.reviewers
    assert result.reasoning


def test_reviewer_suggestion_to_comment():
    from pr_pilot.analyzer import ReviewerSuggestion
    s = ReviewerSuggestion(reviewers=["bob"], reasoning="Bob owns the changed files")
    comment = s.to_comment()
    assert REVIEWER_COMMENT_HEADER in comment
    assert "@bob" in comment
    assert "Bob owns" in comment


# ── Standup generator tests ───────────────────────────────────────────────────

@patch("pr_pilot.analyzer._git", side_effect=lambda *a: {
    ("config", "user.name"): "Alice",
}.get(a, "abc123 Fix login bug\ndef456 Add dark mode"))
def test_generate_standup(mock_git):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("**Yesterday:** Fixed login bug and added dark mode.\n**Today:** Writing tests.\n**Blockers:** None.")
        result = generate_standup("fake-key", days=1)
    assert "Yesterday" in result
    assert "Today" in result


@patch("pr_pilot.analyzer._git", return_value="")
def test_generate_standup_no_commits(mock_git):
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("")
        result = generate_standup("fake-key", days=1)
    assert result == "No recent commits found."


# ── TODO/FIXME issue creator tests ───────────────────────────────────────────

def test_scan_todos_finds_items(tmp_path):
    from pr_pilot.analyzer import _scan_todos
    (tmp_path / "app.py").write_text("x = 1\n# TODO: fix this properly\ny = 2\n")
    results = _scan_todos(str(tmp_path))
    assert len(results) == 1
    assert "fix this properly" in results[0][2]
    assert results[0][1] == 2  # line number


def test_scan_todos_skips_node_modules(tmp_path):
    from pr_pilot.analyzer import _scan_todos
    nm = tmp_path / "node_modules" / "lib"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("// TODO: upstream bug\n")
    results = _scan_todos(str(tmp_path))
    assert results == []


def test_create_issues_from_todos(tmp_path):
    (tmp_path / "utils.py").write_text("def foo():\n    # FIXME: this is slow\n    pass\n")
    payload = {"title": "Fix slow foo()", "body": "The function is slow.", "labels": ["technical-debt"]}
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        issues = create_issues_from_todos("fake-key", root=str(tmp_path))
    assert len(issues) == 1
    assert issues[0].title == "Fix slow foo()"
    assert "technical-debt" in issues[0].labels


# ── Commit message generator tests ───────────────────────────────────────────

@patch("pr_pilot.analyzer._get_staged_diff", return_value="+ def login(user, password):\n+     return auth(user)")
def test_generate_commit_message_basic(mock_diff):
    payload = {
        "subject": "feat(auth): add login function",
        "body": "Implements basic login using the auth helper.",
        "breaking": False,
        "footer": None,
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        msg = generate_commit_message("fake-key")
    assert msg.subject == "feat(auth): add login function"
    assert msg.breaking is False
    assert msg.footer is None


@patch("pr_pilot.analyzer._get_staged_diff", return_value="")
def test_generate_commit_message_no_staged(mock_diff):
    with pytest.raises(ValueError, match="No staged changes"):
        generate_commit_message("fake-key")


def test_commit_message_format_full():
    msg = CommitMessage(
        subject="feat(api): add rate limiting",
        body="Adds sliding window rate limiting to all endpoints.",
        breaking=True,
        footer="BREAKING CHANGE: Rate limit headers are now always present.",
    )
    formatted = msg.format()
    assert "feat(api): add rate limiting" in formatted
    assert "sliding window" in formatted
    assert "BREAKING CHANGE" in formatted


def test_commit_message_format_subject_only():
    msg = CommitMessage(subject="chore: update deps", body=None, breaking=False, footer=None)
    assert msg.format() == "chore: update deps"


# ── Release workflow tests ────────────────────────────────────────────────────

@patch("pr_pilot.analyzer._get_commits_since_tag", return_value="abc Fix login\ndef Add dark mode")
@patch("pr_pilot.analyzer._get_current_version", return_value="1.0.0")
@patch("pr_pilot.analyzer.get_diff", return_value="+ new code")
@patch("pr_pilot.analyzer.get_commits", return_value="")
@patch("pr_pilot.analyzer.get_branch", return_value="main")
def test_run_release_dry_run(mock_branch, mock_commits, mock_diff, mock_ver, mock_ctag, tmp_path):  # noqa: E501
    changelog_payload = {
        "version": "minor", "highlights": "New features.",
        "added": ["Dark mode"], "changed": [], "fixed": ["Login bug"],
        "removed": [], "security": [],
    }
    release_payload = {
        "name": "v1.1.0 — Dark Mode",
        "body": "## What's new\n- Dark mode added\n- Login bug fixed",
        "prerelease": False,
    }
    responses = [
        _mock_openai_response(json.dumps(changelog_payload)),
        _mock_openai_response(json.dumps(release_payload)),
    ]
    changelog_file = str(tmp_path / "CHANGELOG.md")
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.side_effect = responses
        release = run_release("fake-key", repo="owner/repo",
                              changelog_path=changelog_file, dry_run=True)
    assert release.version == "1.1.0"
    assert release.tag == "v1.1.0"
    assert "Dark Mode" in release.name
    # dry run: changelog file should NOT be written
    import pathlib
    assert not pathlib.Path(changelog_file).exists()


# ── Docstring generator tests ─────────────────────────────────────────────────

def test_detect_language():
    assert _detect_language("app.py") == "python"
    assert _detect_language("index.ts") == "typescript"
    assert _detect_language("utils.tsx") == "typescript"
    assert _detect_language("main.js") == "javascript"


def test_extract_functions_python():
    code = "x = 1\n\ndef foo(a, b):\n    return a + b\n\ndef bar():\n    pass\n"
    funcs = _extract_functions(code, "python")
    assert len(funcs) == 2
    assert "def foo" in funcs[0][0]
    assert funcs[0][1] == 3  # line number


def test_generate_docstrings(tmp_path):
    py_file = tmp_path / "utils.py"
    py_file.write_text("def add(a, b):\n    return a + b\n")
    payload = {
        "language": "python",
        "function_name": "add",
        "docstring": "Add two numbers and return the result.",
        "placement": "inside",
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        results = generate_docstrings("fake-key", str(py_file))
    assert len(results) == 1
    assert results[0].function_name == "add"
    assert "Add two numbers" in results[0].docstring
    assert results[0].placement == "inside"


def test_generate_docstrings_no_functions(tmp_path):
    py_file = tmp_path / "constants.py"
    py_file.write_text("MAX = 100\nMIN = 0\n")
    results = generate_docstrings("fake-key", str(py_file))
    assert results == []


# ── Branch namer tests ────────────────────────────────────────────────────────

def test_suggest_branch_basic():
    payload = {
        "suggestions": ["feat/add-dark-mode", "feat/dark-mode-toggle", "feat/theme-switcher"],
        "recommended": 0,
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        result = suggest_branch("fake-key", task="add dark mode to settings page")
    assert result.best == "feat/add-dark-mode"
    assert len(result.suggestions) == 3
    assert result.recommended == 0


def test_suggest_branch_recommended_index():
    payload = {
        "suggestions": ["fix/login-bug", "fix/auth-timeout", "fix/session-expiry"],
        "recommended": 2,
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        result = suggest_branch("fake-key", task="fix session expiry on mobile")
    assert result.best == "fix/session-expiry"


# ── Code explainer tests ──────────────────────────────────────────────────────

def test_explain_code_file(tmp_path):
    py_file = tmp_path / "auth.py"
    py_file.write_text("def authenticate(user, password):\n    return user == 'admin'\n")
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("This file handles basic authentication by comparing credentials.")
        result = explain_code("fake-key", str(py_file))
    assert "authentication" in result.lower()


def test_explain_code_with_selector(tmp_path):
    py_file = tmp_path / "utils.py"
    py_file.write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response("The add function takes two numbers and returns their sum.")
        result = explain_code("fake-key", str(py_file), selector="add")
    assert "add" in result.lower() or "sum" in result.lower()


# ── Test case generator tests ─────────────────────────────────────────────────

def test_generate_tests_python(tmp_path):
    py_file = tmp_path / "math_utils.py"
    py_file.write_text("def divide(a, b):\n    if b == 0:\n        raise ValueError('zero')\n    return a / b\n")
    payload = {
        "framework": "pytest",
        "filename": "test_math_utils.py",
        "code": "import pytest\nfrom math_utils import divide\n\ndef test_divide_basic():\n    assert divide(10, 2) == 5.0\n\ndef test_divide_by_zero():\n    with pytest.raises(ValueError):\n        divide(1, 0)\n",
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        result = generate_tests("fake-key", str(py_file))
    assert result.framework == "pytest"
    assert result.filename == "test_math_utils.py"
    assert "def test_divide_basic" in result.code
    assert "pytest.raises" in result.code


def test_generate_tests_strips_markdown_fence(tmp_path):
    py_file = tmp_path / "utils.py"
    py_file.write_text("def add(a, b): return a + b\n")
    payload = {"framework": "pytest", "filename": "test_utils.py", "code": "def test_add(): assert add(1,2)==3"}
    raw = f"```json\n{json.dumps(payload)}\n```"
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(raw)
        result = generate_tests("fake-key", str(py_file))
    assert result.framework == "pytest"


# ── Security scanner tests ────────────────────────────────────────────────────

@patch("pr_pilot.analyzer.get_diff", return_value='+ password = "hunter2"\n+ query = "SELECT * FROM users WHERE id=" + user_id')
def test_scan_security_finds_issues(mock_diff):
    payload = {
        "issues": [
            {"severity": "critical", "type": "Hardcoded secret",
             "location": "config.py:1", "description": "Password hardcoded in source.",
             "fix": "Move to environment variable."},
            {"severity": "high", "type": "SQL injection",
             "location": "db.py:2", "description": "User input concatenated into query.",
             "fix": "Use parameterized queries."},
        ],
        "summary": "2 critical issues found.",
    }
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        report = scan_security("fake-key")
    assert len(report.issues) == 2
    assert report.has_critical_or_high is True
    assert report.issues[0].severity == "critical"
    assert report.issues[1].type == "SQL injection"


@patch("pr_pilot.analyzer.get_diff", return_value="+ x = 1  # simple change")
def test_scan_security_clean_diff(mock_diff):
    payload = {"issues": [], "summary": "No security issues found."}
    with patch("pr_pilot.analyzer.OpenAI") as MockOpenAI:
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _mock_openai_response(json.dumps(payload))
        report = scan_security("fake-key")
    assert report.issues == []
    assert report.has_critical_or_high is False


def test_security_report_to_comment_clean():
    report = SecurityReport(issues=[], summary="All clear.")
    comment = report.to_comment()
    assert SECURITY_COMMENT_HEADER in comment
    assert "No security issues detected" in comment


def test_security_report_to_comment_with_issues():
    report = SecurityReport(
        issues=[SecurityIssue(
            severity="high", type="SQL injection",
            location="db.py:42", description="Raw SQL.", fix="Use ORM."
        )],
        summary="1 issue found."
    )
    comment = report.to_comment()
    assert "SQL injection" in comment
    assert "HIGH" in comment
    assert "db.py:42" in comment


@patch("pr_pilot.analyzer.get_diff", return_value="")
def test_scan_security_no_diff(mock_diff):
    report = scan_security("fake-key")
    assert report.summary == "No diff to scan."
