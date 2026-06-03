from __future__ import annotations
import argparse
import os
import sys

from .analyzer import (
    describe_pr, suggest_labels, review_pr, review_pr_as_comment,
    generate_changelog, suggest_reviewers, generate_standup, create_issues_from_todos,
    generate_commit_message, run_release,
)

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_GREEN = "\033[32m"
_CYAN  = "\033[36m"
_DIM   = "\033[2m"
_YELLOW = "\033[33m"


def _key() -> str:
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k:
        print("pr-pilot: OPENAI_API_KEY is not set", file=sys.stderr)
        sys.exit(1)
    return k


def cmd_describe(args: argparse.Namespace) -> None:
    print(f"\n  {_DIM}Analyzing diff against '{args.base}'...{_RESET}\n")
    desc = describe_pr(_key(), base=args.base, model=args.model)

    print(f"{_BOLD}Title:{_RESET}  {desc.title}\n")
    print(f"{_BOLD}Summary:{_RESET}")
    print(f"  {desc.summary}\n")
    print(f"{_BOLD}Changes:{_RESET}")
    for c in desc.changes:
        print(f"  {_GREEN}•{_RESET} {c}")
    if desc.breaking:
        print(f"\n  {_YELLOW}⚠ Breaking:{_RESET} {desc.breaking_notes}")
    print(f"\n{_BOLD}Test plan:{_RESET}")
    print(f"  {desc.test_plan}")
    print(f"\n{_BOLD}Suggested labels:{_RESET} {', '.join(desc.labels)}")

    if args.markdown:
        path = args.markdown
        with open(path, "w") as f:
            f.write(desc.to_markdown())
        print(f"\n  {_GREEN}✓{_RESET} Written to {path}")
    print()


def cmd_review(args: argparse.Namespace) -> None:
    print(f"\n  {_DIM}Reviewing diff against '{args.base}'...{_RESET}\n")
    review = review_pr(_key(), base=args.base, model=args.model)
    print(review)
    print()


def cmd_comment(args: argparse.Namespace) -> None:
    """Post AI review as a PR comment on GitHub."""
    from .github_client import upsert_comment
    from .templates import REVIEW_COMMENT_HEADER

    repo   = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    pr_num = args.pr or int(os.environ.get("PR_NUMBER", "0"))

    if not repo or not pr_num:
        print("pr-pilot comment: --repo and --pr are required (or set GITHUB_REPOSITORY / PR_NUMBER)", file=sys.stderr)
        sys.exit(1)

    print(f"\n  {_DIM}Generating review for PR #{pr_num}...{_RESET}\n")
    comment_body = review_pr_as_comment(_key(), base=args.base, model=args.model)
    url = upsert_comment(repo, pr_num, comment_body, REVIEW_COMMENT_HEADER)
    print(f"  {_GREEN}✓{_RESET} Review posted: {url}\n")


def cmd_changelog(args: argparse.Namespace) -> None:
    import datetime
    print(f"\n  {_DIM}Generating changelog from commits...{_RESET}\n")
    entry, new_version = generate_changelog(_key(), model=args.model)
    today = datetime.date.today().isoformat()
    md = entry.to_markdown(new_version, today)

    print(f"{_BOLD}Suggested version bump:{_RESET} {entry.version_bump}  →  {_GREEN}{new_version}{_RESET}")
    print(f"{_BOLD}Highlights:{_RESET} {entry.highlights}\n")
    print(md)

    if args.output:
        changelog_path = args.output
        import pathlib
        p = pathlib.Path(changelog_path)
        if p.exists():
            existing = p.read_text()
            # Insert after the first line (# Changelog header) if present
            if existing.startswith("# "):
                header, rest = existing.split("\n", 1)
                new_content = f"{header}\n\n{md}\n{rest}"
            else:
                new_content = f"{md}\n\n{existing}"
        else:
            new_content = f"# Changelog\n\n{md}\n"
        p.write_text(new_content)
        print(f"\n  {_GREEN}✓{_RESET} Prepended to {changelog_path}")
    print()


def cmd_commit(args: argparse.Namespace) -> None:
    print(f"\n  {_DIM}Analyzing staged changes...{_RESET}\n")
    msg = generate_commit_message(_key(), model=args.model)

    print(f"{_BOLD}Commit message:{_RESET}\n")
    print(f"  {_GREEN}{msg.subject}{_RESET}")
    if msg.body:
        print()
        for line in msg.body.splitlines():
            print(f"  {line}")
    if msg.footer:
        print(f"\n  {_YELLOW}{msg.footer}{_RESET}")
    if msg.breaking:
        print(f"\n  {_YELLOW}⚠ Breaking change{_RESET}")

    if args.commit:
        import subprocess
        full = msg.format()
        result = subprocess.run(["git", "commit", "-m", full])
        if result.returncode == 0:
            print(f"\n  {_GREEN}✓{_RESET} Committed.")
        else:
            print(f"\n  Commit failed — copy the message above and run git commit manually.")
    elif args.copy:
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=msg.format().encode(), check=True)
            print(f"\n  {_GREEN}✓{_RESET} Copied to clipboard")
        except Exception:
            print(f"\n  {_DIM}(--copy requires macOS pbcopy){_RESET}")
    print()


def cmd_release(args: argparse.Namespace) -> None:
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("pr-pilot release: --repo is required (or set GITHUB_REPOSITORY)", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"\n  {_DIM}Dry run — nothing will be committed or published{_RESET}\n")
    else:
        print(f"\n  {_DIM}Running full release workflow for {repo}...{_RESET}\n")

    release = run_release(
        _key(),
        repo=repo,
        changelog_path=args.changelog,
        model=args.model,
        dry_run=args.dry_run,
    )

    print(f"  {_BOLD}Version:{_RESET}  {_GREEN}{release.tag}{_RESET}")
    print(f"  {_BOLD}Name:{_RESET}     {release.name}\n")
    print(release.body)

    if args.dry_run:
        print(f"\n  {_YELLOW}Dry run complete — no changes made.{_RESET}")
    else:
        print(f"\n  {_GREEN}✓{_RESET} CHANGELOG.md updated")
        print(f"  {_GREEN}✓{_RESET} Committed and tagged {release.tag}")
        print(f"  {_GREEN}✓{_RESET} GitHub release created")
    print()


def cmd_reviewers(args: argparse.Namespace) -> None:
    from .github_client import upsert_comment
    from .templates import REVIEWER_COMMENT_HEADER

    suggestion = suggest_reviewers(_key(), base=args.base, model=args.model)

    print(f"\n  {_BOLD}Suggested reviewers:{_RESET}")
    for r in suggestion.reviewers:
        print(f"  {_GREEN}•{_RESET} @{r}")
    print(f"\n  {_DIM}{suggestion.reasoning}{_RESET}")

    if args.post:
        repo   = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        pr_num = args.pr   or int(os.environ.get("PR_NUMBER", "0"))
        if not repo or not pr_num:
            print("  --post requires --repo and --pr (or GITHUB_REPOSITORY/PR_NUMBER)", file=sys.stderr)
            sys.exit(1)
        url = upsert_comment(repo, pr_num, suggestion.to_comment(), REVIEWER_COMMENT_HEADER)
        print(f"\n  {_GREEN}✓{_RESET} Comment posted: {url}")

        if args.assign and suggestion.reviewers:
            from .github_client import _api
            _api("POST", f"/repos/{repo}/pulls/{pr_num}/requested_reviewers",
                 {"reviewers": suggestion.reviewers})
            print(f"  {_GREEN}✓{_RESET} Reviewers assigned: {', '.join(suggestion.reviewers)}")
    print()


def cmd_standup(args: argparse.Namespace) -> None:
    print(f"\n  {_DIM}Generating standup from last {args.days} day(s) of commits...{_RESET}\n")
    update = generate_standup(_key(), days=args.days, model=args.model)
    print(update)
    if args.copy:
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=update.encode(), check=True)
            print(f"\n  {_GREEN}✓{_RESET} Copied to clipboard")
        except Exception:
            print(f"\n  {_DIM}(--copy requires macOS pbcopy){_RESET}")
    print()


def cmd_todos(args: argparse.Namespace) -> None:
    from .github_client import _api

    print(f"\n  {_DIM}Scanning for TODO/FIXME comments in {args.path}...{_RESET}\n")
    issues = create_issues_from_todos(_key(), root=args.path, model=args.model)

    if not issues:
        print("  No TODO/FIXME comments found.")
        return

    print(f"  Found {_BOLD}{len(issues)}{_RESET} item(s):\n")
    for i, issue in enumerate(issues, 1):
        print(f"  {_BOLD}{i}.{_RESET} {issue.title}")
        print(f"     {_DIM}{issue.file_path}:{issue.line_number}{_RESET}  "
              f"{_CYAN}[{', '.join(issue.labels)}]{_RESET}")

    if args.create:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            print("\n  --create requires --repo or GITHUB_REPOSITORY", file=sys.stderr)
            sys.exit(1)
        print()
        created = 0
        for issue in issues:
            # Ensure labels exist
            for label in issue.labels:
                try:
                    _api("POST", f"/repos/{repo}/labels",
                         {"name": label, "color": "e4e669"})
                except SystemExit:
                    pass
            data = _api("POST", f"/repos/{repo}/issues", {
                "title": issue.title,
                "body": issue.body,
                "labels": issue.labels,
            })
            print(f"  {_GREEN}✓{_RESET} #{data['number']} {issue.title}")
            print(f"     {_DIM}{data['html_url']}{_RESET}")
            created += 1
        print(f"\n  {created} issue(s) created.\n")
    print()


def cmd_action(args: argparse.Namespace) -> None:
    """Run as a GitHub Action — reads env vars set by the Actions runner."""
    import json as _json
    from .github_client import get_pr, update_pr, add_labels

    repo     = os.environ.get("GITHUB_REPOSITORY", "")
    pr_num   = int(os.environ.get("PR_NUMBER", "0"))
    token    = os.environ.get("GITHUB_TOKEN", "")
    skip_labels = os.environ.get("SKIP_LABELS", "false").lower() == "true"
    update_title = os.environ.get("UPDATE_TITLE", "false").lower() == "true"
    # Allow action.yml to override the model via env var
    if os.environ.get("MODEL"):
        args.model = os.environ["MODEL"]

    if not repo or not pr_num or not token:
        print("pr-pilot action: GITHUB_REPOSITORY, PR_NUMBER, GITHUB_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    pr = get_pr(repo, pr_num)
    print(f"  PR #{pr_num}: {pr.title}")
    print(f"  Base: {pr.base}  Head: {pr.head}")

    desc = describe_pr(_key(), base=pr.base, model=args.model)
    body = desc.to_markdown()

    # Don't overwrite if user already wrote a substantial description
    if pr.body and len(pr.body.strip()) > 100:
        print("  PR already has a description — skipping body update")
        body = None

    update_pr(repo, pr_num, title=desc.title if update_title else None, body=body or pr.body)
    print(f"  ✓ Description updated")

    if not skip_labels:
        add_labels(repo, pr_num, desc.labels)
        print(f"  ✓ Labels added: {', '.join(desc.labels)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pr-pilot",
        description="AI-powered PR descriptions and labels using OpenAI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- describe ---
    p_desc = sub.add_parser("describe", help="Generate a PR description for the current branch")
    p_desc.add_argument("--base", default="main", help="Base branch to diff against (default: main)")
    p_desc.add_argument("--model", default="gpt-4o", help="OpenAI model to use")
    p_desc.add_argument("--markdown", metavar="FILE", help="Write description as markdown to FILE")
    p_desc.set_defaults(func=cmd_describe)

    # --- review ---
    p_rev = sub.add_parser("review", help="Get a quick code review of the current branch")
    p_rev.add_argument("--base", default="main", help="Base branch to diff against (default: main)")
    p_rev.add_argument("--model", default="gpt-4o", help="OpenAI model to use")
    p_rev.set_defaults(func=cmd_review)

    # --- commit ---
    p_commit = sub.add_parser("commit", help="Generate a conventional commit message from staged changes")
    p_commit.add_argument("--model", default="gpt-4o")
    p_commit.add_argument("--commit", action="store_true", help="Run git commit with the generated message")
    p_commit.add_argument("--copy", action="store_true", help="Copy message to clipboard (macOS)")
    p_commit.set_defaults(func=cmd_commit)

    # --- release ---
    p_release = sub.add_parser("release", help="Full release: changelog + git tag + GitHub release")
    p_release.add_argument("--repo", default=None, help="GitHub repo slug (owner/repo)")
    p_release.add_argument("--model", default="gpt-4o")
    p_release.add_argument("--changelog", default="CHANGELOG.md", metavar="FILE",
                           help="Path to CHANGELOG.md (default: CHANGELOG.md)")
    p_release.add_argument("--dry-run", action="store_true",
                           help="Preview release without committing or publishing")
    p_release.set_defaults(func=cmd_release)

    # --- reviewers ---
    p_rev2 = sub.add_parser("reviewers", help="Suggest reviewers based on git blame of changed files")
    p_rev2.add_argument("--base", default="main")
    p_rev2.add_argument("--model", default="gpt-4o")
    p_rev2.add_argument("--post", action="store_true", help="Post suggestion as a PR comment")
    p_rev2.add_argument("--assign", action="store_true", help="Also assign the reviewers on GitHub")
    p_rev2.add_argument("--repo", default=None)
    p_rev2.add_argument("--pr", type=int, default=None)
    p_rev2.set_defaults(func=cmd_reviewers)

    # --- standup ---
    p_stand = sub.add_parser("standup", help="Generate a daily standup from recent commits")
    p_stand.add_argument("--days", type=int, default=1, help="How many days back to look (default: 1)")
    p_stand.add_argument("--model", default="gpt-4o")
    p_stand.add_argument("--copy", action="store_true", help="Copy output to clipboard (macOS)")
    p_stand.set_defaults(func=cmd_standup)

    # --- todos ---
    p_todos = sub.add_parser("todos", help="Scan for TODO/FIXME comments and create GitHub issues")
    p_todos.add_argument("path", nargs="?", default=".", help="Directory to scan (default: .)")
    p_todos.add_argument("--model", default="gpt-4o")
    p_todos.add_argument("--create", action="store_true", help="Create GitHub issues from found TODOs")
    p_todos.add_argument("--repo", default=None, help="GitHub repo slug (required with --create)")
    p_todos.set_defaults(func=cmd_todos)

    # --- comment ---
    p_com = sub.add_parser("comment", help="Post an AI review as a GitHub PR comment")
    p_com.add_argument("--base", default="main", help="Base branch to diff against (default: main)")
    p_com.add_argument("--model", default="gpt-4o", help="OpenAI model to use")
    p_com.add_argument("--repo", default=None, help="GitHub repo slug (e.g. owner/repo)")
    p_com.add_argument("--pr", type=int, default=None, help="PR number")
    p_com.set_defaults(func=cmd_comment)

    # --- changelog ---
    p_cl = sub.add_parser("changelog", help="Generate a CHANGELOG.md entry from commits since last tag")
    p_cl.add_argument("--model", default="gpt-4o", help="OpenAI model to use")
    p_cl.add_argument("--output", default=None, metavar="FILE",
                      help="Prepend entry to FILE (e.g. CHANGELOG.md). Prints to stdout if omitted.")
    p_cl.set_defaults(func=cmd_changelog)

    # --- action (internal, called by entrypoint.sh) ---
    p_act = sub.add_parser("action", help="Run as a GitHub Action (internal)")
    p_act.add_argument("--model", default="gpt-4o")
    p_act.set_defaults(func=cmd_action)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
