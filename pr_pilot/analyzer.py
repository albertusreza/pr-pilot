from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass, field

from openai import OpenAI

from .templates import DESCRIBE_SYSTEM, DESCRIBE_USER, LABEL_SYSTEM, REVIEW_SYSTEM

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
