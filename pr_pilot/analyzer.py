from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass, field

from openai import OpenAI

from .templates import (
    DESCRIBE_SYSTEM, DESCRIBE_USER, LABEL_SYSTEM, REVIEW_SYSTEM,
    CHANGELOG_SYSTEM, CHANGELOG_USER, REVIEW_COMMENT_HEADER, REVIEW_COMMENT_TEMPLATE,
    REVIEWER_SYSTEM, REVIEWER_USER, REVIEWER_COMMENT_HEADER, REVIEWER_COMMENT_TEMPLATE,
    STANDUP_SYSTEM, STANDUP_USER,
    ISSUE_SYSTEM, ISSUE_USER,
    COMMIT_SYSTEM, COMMIT_USER,
    RELEASE_NOTES_SYSTEM, RELEASE_NOTES_USER,
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

    def to_markdown(self) -> str:
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
    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[diff truncated — showing first 24k chars]"
    return diff


def get_commits(base: str = "main") -> str:
    return _git("log", f"{base}...HEAD", "--oneline", "--no-merges")


def get_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def describe_pr(api_key: str, base: str = "main", model: str = _MODEL) -> PRDescription:
    client = OpenAI(api_key=api_key)
    diff = get_diff(base)
    commits = get_commits(base)
    branch = get_branch()

    if not diff and not commits:
        raise ValueError(f"No changes found between '{branch}' and '{base}'")

    user_msg = DESCRIBE_USER.format(
        branch=branch, base=base, commits=commits or "(none)", diff=diff or "(empty)"
    )
    resp = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": DESCRIBE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])

    data = json.loads(raw.strip())
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


def generate_commit_message(api_key: str, model: str = _MODEL) -> CommitMessage:
    client = OpenAI(api_key=api_key)
    diff = _get_staged_diff()
    if not diff:
        raise ValueError("No staged changes found. Use 'git add' first.")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=512,
        messages=[
            {"role": "system", "content": COMMIT_SYSTEM},
            {"role": "user", "content": COMMIT_USER.format(diff=diff)},
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
