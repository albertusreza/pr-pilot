from __future__ import annotations
import argparse
import os
import sys

from .analyzer import describe_pr, suggest_labels, review_pr, review_pr_as_comment, generate_changelog

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


def cmd_action(args: argparse.Namespace) -> None:
    """Run as a GitHub Action — reads env vars set by the Actions runner."""
    import json as _json
    from .github_client import get_pr, update_pr, add_labels

    repo     = os.environ.get("GITHUB_REPOSITORY", "")
    pr_num   = int(os.environ.get("PR_NUMBER", "0"))
    token    = os.environ.get("GITHUB_TOKEN", "")
    skip_labels = os.environ.get("SKIP_LABELS", "false").lower() == "true"
    update_title = os.environ.get("UPDATE_TITLE", "false").lower() == "true"

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
