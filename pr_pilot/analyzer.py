from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass, field

from openai import OpenAI

from .templates import (
    DESCRIBE_SYSTEM, DESCRIBE_USER,
    DESCRIBE_TEMPLATE_SYSTEM, DESCRIBE_TEMPLATE_USER,
    LABEL_SYSTEM, REVIEW_SYSTEM,
    REVIEW_INLINE_SYSTEM, REVIEW_INLINE_USER, _SEVERITY_LABEL,
    CHANGELOG_SYSTEM, CHANGELOG_USER, REVIEW_COMMENT_HEADER, REVIEW_COMMENT_TEMPLATE,
    REVIEWER_SYSTEM, REVIEWER_USER, REVIEWER_COMMENT_HEADER, REVIEWER_COMMENT_TEMPLATE,
    STANDUP_SYSTEM, STANDUP_USER,
    ISSUE_SYSTEM, ISSUE_USER,
    COMMIT_SYSTEM, COMMIT_USER,
    RELEASE_NOTES_SYSTEM, RELEASE_NOTES_USER,
    DOCSTRING_SYSTEM, DOCSTRING_USER,
    BRANCH_SYSTEM, BRANCH_USER,
    EXPLAIN_SYSTEM, EXPLAIN_USER,
    TEST_SYSTEM, TEST_USER,
    SECURITY_SYSTEM, SECURITY_COMMENT_HEADER, SECURITY_COMMENT_TEMPLATE, _SEVERITY_EMOJI,
)

_MAX_DIFF_CHARS = 24_000   # stay well within context limits
_MODEL = "gpt-4o"


@dataclass
class PRDescription:
    title: str
    summary: str
    changes: list[str]
    breaking: bool
    breaking_notes: str | None
    test_plan: str
    labels: list[str]
    _raw_body: str | None = field(default=None, repr=False)

    def to_markdown(self) -> str:
        # If we have a pre-rendered template body, use it directly
        if self._raw_body:
            return self._raw_body
        lines = [
            f"## Summary\n{self.summary}\n",
            "## Changes",
        ]
        for c in self.changes:
            lines.append(f"- {c}")
        if self.breaking:
            lines.append(f"\n## ⚠️ Breaking Changes\n{self.breaking_notes}")
        lines.append(f"\n## Test Plan\n{self.test_plan}")
        return "\n".join(lines)


def _git(*args: str) -> str:
    result = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    return result.stdout.strip()


def get_diff(base: str = "main") -> str:
    diff = _git("diff", f"{base}...HEAD")
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    # Smart truncation: show full diff per file, drop files once budget runs out
    files = _git("diff", f"{base}...HEAD", "--name-only").splitlines()
    sections = []
    budget = _MAX_DIFF_CHARS - 200
    for f in files:
        file_diff = _git("diff", f"{base}...HEAD", "--", f)
        if len(file_diff) > 8_000:
            # Trim very large single-file diffs to first 8k
            file_diff = file_diff[:8_000] + "\n... [file diff truncated]"
        if budget - len(file_diff) < 0:
            remaining = len(files) - len(sections)
            sections.append(f"\n[{remaining} more file(s) omitted — diff too large]")
            break
        sections.append(file_diff)
        budget -= len(file_diff)
    return "\n".join(sections)


def get_diff_stat(base: str = "main") -> str:
    """Return human-readable diff stats (files changed, insertions, deletions)."""
    return _git("diff", f"{base}...HEAD", "--stat")


def get_commits(base: str = "main") -> str:
    return _git("log", f"{base}...HEAD", "--oneline", "--no-merges")


def get_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def _find_pr_template() -> str | None:
    """Search for a PR template in common locations. Returns content or None."""
    from pathlib import Path
    candidates = [
        ".github/pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/PULL_REQUEST_TEMPLATE/default.md",
        "docs/pull_request_template.md",
        "PULL_REQUEST_TEMPLATE.md",
    ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            content = p.read_text(errors="replace").strip()
            if content:
                return content
    return None


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    return json.loads(raw.strip())


def describe_pr(
    api_key: str,
    base: str = "main",
    model: str = _MODEL,
    use_template: bool = True,
) -> PRDescription:
    client = OpenAI(api_key=api_key)
    diff = get_diff(base)
    diff_stat = get_diff_stat(base)
    commits = get_commits(base)
    branch = get_branch()

    if not diff and not commits:
        raise ValueError(f"No changes found between '{branch}' and '{base}'")

    template = _find_pr_template() if use_template else None

    if template:
        user_msg = DESCRIBE_TEMPLATE_USER.format(
            branch=branch, base=base,
            commits=commits or "(none)",
            diff_stat=diff_stat or "(no stats)",
            diff=diff or "(empty)",
            template=template,
        )
        system = DESCRIBE_TEMPLATE_SYSTEM
    else:
        user_msg = DESCRIBE_USER.format(
            branch=branch, base=base,
            commits=commits or "(none)",
            diff_stat=diff_stat or "(no stats)",
            diff=diff or "(empty)",
        )
        system = DESCRIBE_SYSTEM

    resp = client.chat.completions.create(
        model=model,
        max_tokens=1536,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    )
    data = _parse_json_response(resp.choices[0].message.content or "{}")

    # Template mode returns a pre-rendered body; store it for to_markdown()
    if template and "body" in data:
        return PRDescription(
            title=data.get("title", branch),
            summary=data.get("body", ""),   # body goes into summary slot
            changes=[],                      # already embedded in body
            breaking=False,
            breaking_notes=None,
            test_plan="",
            labels=data.get("labels", ["feature"]),
            _raw_body=data.get("body"),      # type: ignore[call-arg]
        )

    return PRDescription(
        title=data.get("title", branch),
        summary=data.get("summary", ""),
        changes=data.get("changes", []),
        breaking=data.get("breaking", False),
        breaking_notes=data.get("breaking_notes"),
        test_plan=data.get("test_plan", ""),
        labels=data.get("labels", ["feature"]),
    )


def suggest_labels(api_key: str, title: str, body: str, model: str = _MODEL) -> list[str]:
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=64,
        messages=[
            {"role": "system", "content": LABEL_SYSTEM},
            {"role": "user", "content": f"Title: {title}\n\nBody: {body[:2000]}"},
        ],
    )
    raw = resp.choices[0].message.content or "[]"
    return json.loads(raw.strip())


def review_pr(api_key: str, base: str = "main", model: str = _MODEL) -> str:
    client = OpenAI(api_key=api_key)
    diff = get_diff(base)
    if not diff:
        return "No changes to review."
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": f"Diff:\n\n{diff}"},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def review_pr_as_comment(api_key: str, base: str = "main", model: str = _MODEL) -> str:
    """Return a GitHub-flavoured markdown comment with the AI review."""
    body = review_pr(api_key, base=base, model=model)
    return REVIEW_COMMENT_TEMPLATE.format(header=REVIEW_COMMENT_HEADER, body=body)


# ── Inline review ─────────────────────────────────────────────────────────────

import re as _re
_HUNK_RE = _re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _parse_diff_lines(diff: str) -> dict[str, set[int]]:
    """Parse a unified diff and return {file_path: {new_file_line_numbers}}.

    Includes both added (+) and context lines — the set of lines valid for
    GitHub review comments (which must exist in the new-file side of the diff).
    """
    result: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, set())
        elif line.startswith("@@ ") and current is not None:
            m = _HUNK_RE.match(line)
            if m:
                new_line = int(m.group(1))
        elif current is not None:
            if line.startswith("-"):
                pass               # deleted: no new-file line
            elif line.startswith("+"):
                result[current].add(new_line)
                new_line += 1
            elif line.startswith("\\"):
                pass               # "\ No newline at end of file"
            else:
                result[current].add(new_line)   # context
                new_line += 1
    return result


def _annotate_diff_for_review(base: str = "main") -> tuple[str, dict[str, set[int]]]:
    """Return (annotated_diff_str, diff_lines_map).

    Each line in the annotated diff is prefixed with [+N] (added) or [ N] (context)
    so the LLM can reference exact new-file line numbers.
    """
    diff = get_diff(base)
    line_map = _parse_diff_lines(diff)
    lines_out: list[str] = []
    current: str | None = None
    new_line = 0

    for line in diff.splitlines():
        if line.startswith("diff --git"):
            lines_out.append(line)
        elif line.startswith("+++ b/"):
            current = line[6:]
            lines_out.append(f"\n=== {current} ===")
        elif line.startswith("--- ") or line.startswith("+++ "):
            pass    # skip --- a/... headers (already shown above)
        elif line.startswith("@@ ") and current is not None:
            m = _HUNK_RE.match(line)
            if m:
                new_line = int(m.group(1))
            lines_out.append(line)
        elif current is not None:
            if line.startswith("-"):
                lines_out.append(f"  {line}")          # deleted — no number
            elif line.startswith("+"):
                lines_out.append(f"[+{new_line:4d}] {line[1:]}")
                new_line += 1
            elif line.startswith("\\"):
                pass
            else:
                lines_out.append(f"[ {new_line:4d}] {line[1:]}")
                new_line += 1
        else:
            lines_out.append(line)

    return "\n".join(lines_out), line_map


@dataclass
class ReviewComment:
    path: str
    line: int
    severity: str    # "must-fix" | "suggestion" | "nit"
    body: str


@dataclass
class InlineReview:
    summary: str
    comments: list[ReviewComment]

    def to_fallback_comment(self) -> str:
        """Render as a single markdown comment (fallback when not posting to GitHub)."""
        lines = [REVIEW_COMMENT_HEADER, "### 🤖 pr-pilot inline review\n", self.summary]
        if self.comments:
            lines.append("")
            for c in self.comments:
                label = _SEVERITY_LABEL.get(c.severity, c.severity)
                lines.append(f"**{label}** · `{c.path}:{c.line}`")
                lines.append(f"> {c.body}")
                lines.append("")
        lines.append("---")
        lines.append(
            "<sub>Generated by [pr-pilot](https://github.com/albertusreza/pr-pilot) "
            "· [pullwise](https://pypi.org/project/pullwise/) on PyPI</sub>"
        )
        return "\n".join(lines)


def review_pr_inline(
    api_key: str,
    base: str = "main",
    model: str = _MODEL,
) -> InlineReview:
    """Run a structured inline review — returns comment objects with file+line."""
    client = OpenAI(api_key=api_key)
    annotated_diff, line_map = _annotate_diff_for_review(base)
    if not annotated_diff.strip():
        return InlineReview(summary="No changes to review.", comments=[])

    resp = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": REVIEW_INLINE_SYSTEM},
            {"role": "user", "content": REVIEW_INLINE_USER.format(
                annotated_diff=annotated_diff[:20_000]
            )},
        ],
    )
    data = _parse_json_response(resp.choices[0].message.content or "{}")
    raw_comments = data.get("comments", [])

    # Validate line numbers — only keep comments on lines that exist in the diff
    validated: list[ReviewComment] = []
    for c in raw_comments:
        path = c.get("path", "")
        line = int(c.get("line", 0))
        valid_lines = line_map.get(path, set())
        # Accept exact match or nearest line in the same file's diff lines
        if line in valid_lines:
            actual_line = line
        elif valid_lines:
            actual_line = min(valid_lines, key=lambda x: abs(x - line))
        else:
            continue   # file not in diff — skip
        validated.append(ReviewComment(
            path=path,
            line=actual_line,
            severity=c.get("severity", "suggestion"),
            body=c.get("body", ""),
        ))

    return InlineReview(
        summary=data.get("summary", ""),
        comments=validated,
    )


# ── Changelog ────────────────────────────────────────────────────────────────

@dataclass
class ChangelogEntry:
    version_bump: str        # "patch" | "minor" | "major"
    highlights: str
    added: list[str]
    changed: list[str]
    fixed: list[str]
    removed: list[str]
    security: list[str]

    def to_markdown(self, new_version: str, date: str) -> str:
        lines = [f"## [{new_version}] - {date}", f"\n_{self.highlights}_\n"]
        sections = [
            ("Added", self.added),
            ("Changed", self.changed),
            ("Fixed", self.fixed),
            ("Removed", self.removed),
            ("Security", self.security),
        ]
        for title, items in sections:
            if items:
                lines.append(f"\n### {title}")
                for item in items:
                    lines.append(f"- {item}")
        return "\n".join(lines)


def _get_current_version() -> str:
    """Read version from pyproject.toml, setup.cfg, or package __init__."""
    try:
        tag = _git("describe", "--tags", "--abbrev=0")
        return tag.lstrip("v") if tag else "0.0.0"
    except Exception:
        return "0.0.0"


def _get_commits_since_tag() -> str:
    tag = _git("describe", "--tags", "--abbrev=0")
    if tag:
        return _git("log", f"{tag}...HEAD", "--oneline", "--no-merges")
    return _git("log", "--oneline", "--no-merges", "-30")


def _bump_version(current: str, bump: str) -> str:
    parts = current.split(".")
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2].split("-")[0])
    except (IndexError, ValueError):
        return "0.1.0"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def generate_changelog(api_key: str, model: str = _MODEL) -> tuple[ChangelogEntry, str]:
    """Return (ChangelogEntry, new_version_string)."""
    import datetime
    client = OpenAI(api_key=api_key)
    current = _get_current_version()
    commits = _get_commits_since_tag()
    diff = get_diff(base="HEAD~10") if not commits else get_diff(base=f"v{current}" if current != "0.0.0" else "HEAD~10")

    if not commits:
        raise ValueError("No commits found since last tag. Nothing to changelog.")

    user_msg = CHANGELOG_USER.format(
        current_version=current, commits=commits, diff=diff[:12_000] or "(empty)"
    )
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": CHANGELOG_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])

    data = json.loads(raw.strip())
    entry = ChangelogEntry(
        version_bump=data.get("version", "patch"),
        highlights=data.get("highlights", ""),
        added=data.get("added", []),
        changed=data.get("changed", []),
        fixed=data.get("fixed", []),
        removed=data.get("removed", []),
        security=data.get("security", []),
    )
    new_version = _bump_version(current, entry.version_bump)
    return entry, new_version


# ── Reviewer suggester ────────────────────────────────────────────────────────

@dataclass
class ReviewerSuggestion:
    reviewers: list[str]
    reasoning: str

    def to_comment(self) -> str:
        rows = "\n".join(f"| @{r} | {self.reasoning} |" for r in self.reviewers)
        return REVIEWER_COMMENT_TEMPLATE.format(
            header=REVIEWER_COMMENT_HEADER,
            reasoning=self.reasoning,
            rows=rows,
        )


def _get_blame_summary(base: str = "main") -> tuple[str, str]:
    """Return (author, blame_summary) for files changed vs base."""
    author = _git("config", "user.name") or "unknown"
    changed_files = _git("diff", f"{base}...HEAD", "--name-only").splitlines()
    lines = []
    for f in changed_files[:15]:  # cap at 15 files
        blame = _git("log", "--follow", "--format=%an", "-5", "--", f)
        authors = ", ".join(dict.fromkeys(blame.splitlines()))  # unique, ordered
        lines.append(f"  {f}: {authors or 'no history'}")
    return author, "\n".join(lines)


def suggest_reviewers(api_key: str, base: str = "main", model: str = _MODEL) -> ReviewerSuggestion:
    client = OpenAI(api_key=api_key)
    author, blame_summary = _get_blame_summary(base)
    if not blame_summary:
        raise ValueError("No changed files found — nothing to base reviewer suggestion on.")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[
            {"role": "system", "content": REVIEWER_SYSTEM},
            {"role": "user", "content": REVIEWER_USER.format(author=author, blame_summary=blame_summary)},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    return ReviewerSuggestion(
        reviewers=data.get("reviewers", []),
        reasoning=data.get("reasoning", ""),
    )


# ── Standup generator ─────────────────────────────────────────────────────────

def generate_standup(api_key: str, days: int = 1, model: str = _MODEL) -> str:
    client = OpenAI(api_key=api_key)
    author = _git("config", "user.name") or "developer"
    since = f"{days} day ago" if days == 1 else f"{days} days ago"
    commits = _git("log", f"--since={since}", "--oneline", "--no-merges",
                   f"--author={author}")
    if not commits:
        commits = _git("log", "--oneline", "--no-merges", "-10")
    if not commits:
        return "No recent commits found."
    resp = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[
            {"role": "system", "content": STANDUP_SYSTEM},
            {"role": "user", "content": STANDUP_USER.format(
                author=author, days=days, commits=commits
            )},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ── Issue creator (TODO/FIXME scanner) ───────────────────────────────────────

@dataclass
class TodoIssue:
    file_path: str
    line_number: int
    comment: str
    title: str
    body: str
    labels: list[str]


def _scan_todos(root: str = ".") -> list[tuple[str, int, str, str]]:
    """Return list of (file, lineno, comment_text, context) for TODO/FIXME."""
    import re
    from pathlib import Path
    pattern = re.compile(r'(TODO|FIXME|HACK|XXX)\s*:?\s*(.+)', re.IGNORECASE)
    skip = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build'}
    results = []
    for path in Path(root).rglob('*'):
        if any(p in skip for p in path.parts):
            continue
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rb', '.java', '.md'}:
            continue
        try:
            lines = path.read_text(errors='replace').splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            m = pattern.search(line)
            if m:
                start = max(0, i - 3)
                end = min(len(lines), i + 3)
                context = '\n'.join(lines[start:end])
                results.append((str(path), i, m.group(0).strip(), context))
    return results


def create_issues_from_todos(
    api_key: str, root: str = ".", model: str = _MODEL
) -> list[TodoIssue]:
    client = OpenAI(api_key=api_key)
    todos = _scan_todos(root)
    issues = []
    for file_path, line_number, comment, context in todos:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": ISSUE_SYSTEM},
                {"role": "user", "content": ISSUE_USER.format(
                    file_path=file_path, line_number=line_number,
                    comment=comment, context=context,
                )},
            ],
        )
        raw = (resp.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.splitlines()[:-1])
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        issues.append(TodoIssue(
            file_path=file_path,
            line_number=line_number,
            comment=comment,
            title=data.get("title", comment[:72]),
            body=data.get("body", ""),
            labels=data.get("labels", ["technical-debt"]),
        ))
    return issues


# ── Commit message generator ──────────────────────────────────────────────────

@dataclass
class CommitMessage:
    subject: str
    body: str | None
    breaking: bool
    footer: str | None

    def format(self) -> str:
        parts = [self.subject]
        if self.body:
            parts.append("")
            parts.append(self.body)
        if self.footer:
            parts.append("")
            parts.append(self.footer)
        return "\n".join(parts)


def _get_staged_diff() -> str:
    diff = _git("diff", "--cached")
    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[diff truncated]"
    return diff


def _detect_commit_scope() -> str:
    """Infer a conventional-commit scope from staged file paths.

    Examples:
      auth/login.py, auth/signup.py  → "auth"
      src/api/routes.py              → "api"
      tests/test_utils.py            → "tests"

    Returns an empty string if no clear scope is found.
    """
    from collections import Counter
    files = _git("diff", "--cached", "--name-only").splitlines()
    if not files:
        return ""
    # Top-level dirs that are containers, not scopes
    _SKIP = {"src", "lib", "app", "pkg", "packages", "source", ""}
    candidates: list[str] = []
    for f in files:
        parts = f.replace("\\", "/").split("/")
        for part in parts[:-1]:          # skip the filename itself
            token = part.lower()
            if token not in _SKIP:
                candidates.append(token)
                break                    # only take the first meaningful dir
    if not candidates:
        return ""
    top, count = Counter(candidates).most_common(1)[0]
    # Only use as scope if it covers at least half the staged files
    # Require strict majority (>50%) so mixed-dir diffs don't get a spurious scope
    if count * 2 > len(files):
        return top
    return ""


def generate_commit_message(api_key: str, model: str = _MODEL) -> CommitMessage:
    client = OpenAI(api_key=api_key)
    diff = _get_staged_diff()
    if not diff:
        raise ValueError("No staged changes found. Use 'git add' first.")
    scope_hint = _detect_commit_scope() or "none detected"
    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[
            {"role": "system", "content": COMMIT_SYSTEM},
            {"role": "user", "content": COMMIT_USER.format(diff=diff, scope_hint=scope_hint)},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    return CommitMessage(
        subject=data.get("subject", "chore: update"),
        body=data.get("body") or None,
        breaking=data.get("breaking", False),
        footer=data.get("footer") or None,
    )


# ── Full release workflow ─────────────────────────────────────────────────────

@dataclass
class ReleaseInfo:
    version: str
    tag: str
    name: str
    body: str
    prerelease: bool


def _generate_release_notes(
    api_key: str, version: str, changelog_md: str, model: str = _MODEL
) -> tuple[str, str]:
    """Return (release_name, release_body)."""
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": RELEASE_NOTES_SYSTEM},
            {"role": "user", "content": RELEASE_NOTES_USER.format(
                version=version, changelog_md=changelog_md
            )},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    return data.get("name", f"v{version}"), data.get("body", changelog_md)


def run_release(
    api_key: str,
    repo: str,
    changelog_path: str = "CHANGELOG.md",
    model: str = _MODEL,
    dry_run: bool = False,
) -> ReleaseInfo:
    """Full release: generate changelog → bump version → create GitHub release."""
    import datetime
    from .github_client import _api

    # 1. Generate changelog entry
    entry, new_version = generate_changelog(api_key, model=model)
    today = datetime.date.today().isoformat()
    changelog_md = entry.to_markdown(new_version, today)

    # 2. Write CHANGELOG.md
    import pathlib
    p = pathlib.Path(changelog_path)
    if p.exists():
        existing = p.read_text()
        if existing.startswith("# "):
            header, rest = existing.split("\n", 1)
            new_content = f"{header}\n\n{changelog_md}\n{rest}"
        else:
            new_content = f"{changelog_md}\n\n{existing}"
    else:
        new_content = f"# Changelog\n\n{changelog_md}\n"

    if not dry_run:
        p.write_text(new_content)

    # 3. Generate release notes
    release_name, release_body = _generate_release_notes(
        api_key, new_version, changelog_md, model=model
    )
    tag = f"v{new_version}"

    if not dry_run:
        # 4. Commit changelog
        import subprocess
        subprocess.run(["git", "add", changelog_path], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: release {tag}"],
            check=True
        )
        # 5. Create git tag
        subprocess.run(["git", "tag", tag], check=True)
        subprocess.run(["git", "push"], check=True)
        subprocess.run(["git", "push", "--tags"], check=True)
        # 6. Create GitHub release
        _api("POST", f"/repos/{repo}/releases", {
            "tag_name": tag,
            "name": release_name,
            "body": release_body,
            "prerelease": False,
        })

    return ReleaseInfo(
        version=new_version,
        tag=tag,
        name=release_name,
        body=release_body,
        prerelease=False,
    )


# ── Docstring generator ───────────────────────────────────────────────────────

@dataclass
class DocstringResult:
    language: str
    function_name: str
    docstring: str
    placement: str   # "above" | "inside"


def _detect_language(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "py":
        return "python"
    if ext in {"ts", "tsx"}:
        return "typescript"
    return "javascript"


def _extract_functions(code: str, language: str) -> list[tuple[str, int]]:
    """Return list of (function_source, start_line) for top-level functions."""
    import re
    lines = code.splitlines()
    results = []
    if language == "python":
        pattern = re.compile(r"^(def |async def )")
        i = 0
        while i < len(lines):
            if pattern.match(lines[i]):
                start = i
                # collect until next top-level def/class or EOF
                j = i + 1
                while j < len(lines) and (not lines[j] or lines[j][0] in " \t#"):
                    j += 1
                results.append(("\n".join(lines[start:j]), start + 1))
                i = j
            else:
                i += 1
    else:
        pattern = re.compile(r"^(export\s+)?(async\s+)?function\s+\w+|^\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?\(")
        for i, line in enumerate(lines):
            if pattern.match(line):
                end = min(i + 30, len(lines))
                results.append(("\n".join(lines[i:end]), i + 1))
    return results


def generate_docstrings(
    api_key: str, file_path: str, model: str = _MODEL
) -> list[DocstringResult]:
    """Generate docstrings for all functions in a file changed in the diff."""
    from pathlib import Path
    client = OpenAI(api_key=api_key)
    code = Path(file_path).read_text(errors="replace")
    language = _detect_language(file_path)
    functions = _extract_functions(code, language)
    if not functions:
        return []
    results = []
    for func_code, _lineno in functions[:10]:  # cap at 10 per file
        resp = client.chat.completions.create(
            model=model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": DOCSTRING_SYSTEM},
                {"role": "user", "content": DOCSTRING_USER.format(
                    language=language, code=func_code[:3000]
                )},
            ],
        )
        raw = (resp.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.splitlines()[:-1])
        try:
            data = json.loads(raw.strip())
            results.append(DocstringResult(
                language=data.get("language", language),
                function_name=data.get("function_name", "unknown"),
                docstring=data.get("docstring", ""),
                placement=data.get("placement", "inside"),
            ))
        except json.JSONDecodeError:
            continue
    return results


# ── Branch namer ──────────────────────────────────────────────────────────────

@dataclass
class BranchSuggestion:
    suggestions: list[str]
    recommended: int   # index into suggestions

    @property
    def best(self) -> str:
        return self.suggestions[self.recommended]


def suggest_branch(api_key: str, task: str, model: str = _MODEL) -> BranchSuggestion:
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[
            {"role": "system", "content": BRANCH_SYSTEM},
            {"role": "user", "content": BRANCH_USER.format(task=task)},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    suggestions = data.get("suggestions", [f"feat/{task[:40].lower().replace(' ', '-')}"])
    recommended = data.get("recommended", 0)
    return BranchSuggestion(suggestions=suggestions, recommended=recommended)


# ── Code explainer ────────────────────────────────────────────────────────────

def explain_code(
    api_key: str,
    file_path: str,
    selector: str | None = None,
    model: str = _MODEL,
) -> str:
    """Explain a file or specific function in plain English."""
    from pathlib import Path
    client = OpenAI(api_key=api_key)
    code = Path(file_path).read_text(errors="replace")

    # If selector given, try to extract just that function/class
    if selector:
        import re
        pattern = re.compile(
            rf"^(def |async def |class |\w+ = (async )?function )"
            rf".*{re.escape(selector)}",
            re.MULTILINE
        )
        m = pattern.search(code)
        if m:
            start = m.start()
            # grab next ~60 lines
            snippet = "\n".join(code[start:].splitlines()[:60])
            code = snippet

    if len(code) > _MAX_DIFF_CHARS:
        code = code[:_MAX_DIFF_CHARS] + "\n\n[truncated]"

    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[
            {"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": EXPLAIN_USER.format(
                file_path=file_path,
                selector=f"Function/class: {selector}" if selector else "Whole file",
                code=code,
            )},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ── Test case generator ───────────────────────────────────────────────────────

@dataclass
class GeneratedTests:
    framework: str
    filename: str
    code: str


def generate_tests(
    api_key: str,
    file_path: str,
    selector: str | None = None,
    model: str = _MODEL,
) -> GeneratedTests:
    """Generate unit tests for a file or specific function."""
    from pathlib import Path
    client = OpenAI(api_key=api_key)
    language = _detect_language(file_path)
    code = Path(file_path).read_text(errors="replace")

    if selector:
        import re
        pattern = re.compile(
            rf"^(def |async def |class |\w+ = (async )?function )"
            rf".*{re.escape(selector)}",
            re.MULTILINE,
        )
        m = pattern.search(code)
        if m:
            code = "\n".join(code[m.start():].splitlines()[:80])

    if len(code) > _MAX_DIFF_CHARS:
        code = code[:_MAX_DIFF_CHARS] + "\n[truncated]"

    resp = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": TEST_SYSTEM},
            {"role": "user", "content": TEST_USER.format(
                language=language, file_path=file_path, code=code
            )},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    return GeneratedTests(
        framework=data.get("framework", "pytest"),
        filename=data.get("filename", f"test_{file_path.split('/')[-1]}"),
        code=data.get("code", ""),
    )


# ── Security scanner ──────────────────────────────────────────────────────────

@dataclass
class SecurityIssue:
    severity: str
    type: str
    location: str
    description: str
    fix: str


@dataclass
class SecurityReport:
    issues: list[SecurityIssue]
    summary: str

    def to_comment(self) -> str:
        if not self.issues:
            body = "✅ No security issues detected in this diff."
        else:
            rows = []
            for issue in sorted(self.issues, key=lambda x: (
                ["critical", "high", "medium", "low", "info"].index(x.severity)
            )):
                emoji = _SEVERITY_EMOJI.get(issue.severity, "•")
                rows.append(
                    f"#### {emoji} {issue.severity.upper()} — {issue.type}\n"
                    f"**Location:** `{issue.location}`\n\n"
                    f"{issue.description}\n\n"
                    f"**Fix:** {issue.fix}"
                )
            body = "\n\n---\n\n".join(rows)
        return SECURITY_COMMENT_TEMPLATE.format(
            header=SECURITY_COMMENT_HEADER,
            summary=self.summary,
            body=body,
        )

    @property
    def has_critical_or_high(self) -> bool:
        return any(i.severity in ("critical", "high") for i in self.issues)


def scan_security(
    api_key: str,
    base: str = "main",
    model: str = _MODEL,
) -> SecurityReport:
    client = OpenAI(api_key=api_key)
    diff = get_diff(base)
    if not diff:
        return SecurityReport(issues=[], summary="No diff to scan.")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SECURITY_SYSTEM},
            {"role": "user", "content": f"Diff to review:\n\n{diff}"},
        ],
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])
    data = json.loads(raw.strip())
    issues = [
        SecurityIssue(
            severity=i.get("severity", "info"),
            type=i.get("type", "Unknown"),
            location=i.get("location", "unknown"),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("issues", [])
    ]
    return SecurityReport(issues=issues, summary=data.get("summary", ""))
