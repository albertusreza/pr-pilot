from __future__ import annotations
import argparse
import os
import sys

from .analyzer import describe_pr, suggest_labels, review_pr

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

    # --- action (internal, called by entrypoint.sh) ---
    p_act = sub.add_parser("action", help="Run as a GitHub Action (internal)")
    p_act.add_argument("--model", default="gpt-4o")
    p_act.set_defaults(func=cmd_action)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
