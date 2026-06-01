from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class PRInfo:
    number: int
    title: str
    body: str
    base: str
    head: str


def _api(method: str, path: str, body: dict | None = None) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"GitHub API error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def get_pr(repo: str, pr_number: int) -> PRInfo:
    data = _api("GET", f"/repos/{repo}/pulls/{pr_number}")
    return PRInfo(
        number=data["number"],
        title=data["title"],
        body=data.get("body") or "",
        base=data["base"]["ref"],
        head=data["head"]["ref"],
    )


def update_pr(repo: str, pr_number: int, title: str | None, body: str) -> None:
    payload: dict = {"body": body}
    if title:
        payload["title"] = title
    _api("PATCH", f"/repos/{repo}/pulls/{pr_number}", payload)


def add_labels(repo: str, pr_number: int, labels: list[str]) -> None:
    # Ensure labels exist first
    existing = {l["name"] for l in _api("GET", f"/repos/{repo}/labels")}
    _COLORS = {
        "bug": "d73a4a", "feature": "0075ca", "docs": "0075ca",
        "refactor": "e4e669", "test": "bfd4f2", "chore": "ffffff",
        "performance": "f9d0c4", "security": "ee0701", "breaking-change": "b60205",
    }
    for label in labels:
        if label not in existing:
            try:
                _api("POST", f"/repos/{repo}/labels", {
                    "name": label,
                    "color": _COLORS.get(label, "ededed"),
                })
            except SystemExit:
                pass  # label might already exist in race condition

    _api("POST", f"/repos/{repo}/issues/{pr_number}/labels", {"labels": labels})


def post_comment(repo: str, pr_number: int, body: str) -> None:
    _api("POST", f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})
