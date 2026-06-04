# PullWise ✈️

[![PyPI](https://img.shields.io/pypi/v/pullwise)](https://pypi.org/project/pullwise/)
[![Downloads](https://img.shields.io/pypi/dm/pullwise)](https://pypi.org/project/pullwise/)
[![CI](https://github.com/albertusreza/pr-pilot/actions/workflows/ci.yml/badge.svg)](https://github.com/albertusreza/pr-pilot/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Stop writing PR descriptions.** Let AI do it.

> 📦 GitHub Action: [`albertusreza/pr-pilot`](https://github.com/marketplace/actions/pullwise) · PyPI: [`pullwise`](https://pypi.org/project/pullwise/)

`pr-pilot` is a GitHub Action + CLI that analyzes your diff and commit history, then automatically writes a clear, structured PR description — with a summary, change list, test plan, and labels.

```yaml
# .github/workflows/pr-description.yml
- uses: albertusreza/pr-pilot@main
  with:
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

That's it. Every new PR gets a description like this:

---

> **Add rate limiting to upload endpoint**
>
> ## Summary
> Adds per-IP rate limiting to `/api/upload` using a sliding window algorithm.
> Without this, a single client could saturate the server with concurrent uploads.
>
> ## Changes
> - Add `RateLimiter` middleware with configurable window and max requests
> - Apply limiter to `/api/upload` route (100 req/min default)
> - Return `429 Too Many Requests` with `Retry-After` header
> - Add unit tests for edge cases (burst, reset, concurrent)
>
> ## Test Plan
> 1. Run `pytest tests/test_rate_limiter.py`
> 2. Send 101 rapid requests to `/api/upload` — 101st should return 429
> 3. Wait 60 seconds — requests should succeed again
>
> **Labels:** `feature`, `security`

---

## Install

### As a GitHub Action (zero-config)

```yaml
name: PR Pilot
on:
  pull_request:
    types: [opened, reopened]

jobs:
  describe:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: albertusreza/pr-pilot@main
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

Add `OPENAI_API_KEY` to your repo secrets and you're done. Works with any language, any repo size.

### As a CLI

```bash
pip install pullwise
export OPENAI_API_KEY=sk-...

# Generate a description for your current branch
pr-pilot describe

# Diff against a different base
pr-pilot describe --base develop

# Get a quick code review
pr-pilot review

# Save description as markdown
pr-pilot describe --markdown pr_description.md
```

## Features

| Command | What it does |
|---|---|
| `pr-pilot describe` | Generate a structured PR description from your diff |
| `pr-pilot review` | Get a senior-engineer-style code review in the terminal |
| `pr-pilot comment` | Post an AI review as a GitHub PR comment (updates on re-run, no spam) |
| `pr-pilot changelog` | Generate a `CHANGELOG.md` entry from commits since last tag |
| `pr-pilot reviewers` | Suggest reviewers based on git blame of changed files |
| `pr-pilot standup` | Generate a daily standup update from your recent commits |
| `pr-pilot todos` | Scan for TODO/FIXME comments and create GitHub issues from them |
| `pr-pilot commit` | Generate a Conventional Commit message from staged changes |
| `pr-pilot release` | Full release: changelog + git tag + GitHub release in one command |
| `pr-pilot docs` | Generate docstrings for functions in changed files |
| `pr-pilot branch` | Suggest a git branch name from a plain-English task description |
| `pr-pilot explain` | Explain what a file or function does in plain English |

## Usage

```bash
# Generate PR description (terminal)
pr-pilot describe --base main

# Get a quick code review in the terminal
pr-pilot review --base main

# Post review as a GitHub PR comment
pr-pilot comment --repo owner/repo --pr 42

# Generate CHANGELOG entry (print to stdout)
pr-pilot changelog

# Write directly into CHANGELOG.md
pr-pilot changelog --output CHANGELOG.md
```

```bash
# Generate docstrings for all functions changed vs main
pr-pilot docs

# Document a specific file
pr-pilot docs src/auth.py

# Suggest a branch name from a task description
pr-pilot branch "add rate limiting to upload endpoint"

# Create the branch immediately
pr-pilot branch "fix login timeout on mobile" --checkout

# Explain what a file does
pr-pilot explain src/auth.py

# Explain a specific function
pr-pilot explain src/auth.py --function authenticate
```

```bash
# Generate a commit message from staged changes
pr-pilot commit

# Run git commit directly with the generated message
pr-pilot commit --commit

# Full release: bump version + write changelog + create GitHub release
pr-pilot release --repo owner/repo

# Preview release without publishing
pr-pilot release --repo owner/repo --dry-run
```

```bash
# Suggest reviewers for your current branch
pr-pilot reviewers --base main

# Post suggestion as a comment + assign on GitHub
pr-pilot reviewers --post --assign --repo owner/repo --pr 42

# Generate a standup from yesterday's commits
pr-pilot standup

# Generate from the last 3 days and copy to clipboard
pr-pilot standup --days 3 --copy

# Scan for TODO/FIXME and preview issues
pr-pilot todos

# Actually create GitHub issues from them
pr-pilot todos --create --repo owner/repo
```

### Auto-comment on every PR (GitHub Action)

```yaml
# .github/workflows/pr-review.yml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- run: pip install pullwise
- run: pr-pilot comment --repo ${{ github.repository }} --pr ${{ github.event.pull_request.number }}
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    GITHUB_TOKEN: ${{ github.token }}
```

The comment updates itself on every new push — no duplicate comments.

## Options

| Input | Default | Description |
|---|---|---|
| `openai-api-key` | — | **Required.** Your OpenAI API key |
| `model` | `gpt-4o` | OpenAI model to use |
| `skip-labels` | `false` | Skip adding labels to the PR |
| `update-title` | `false` | Also rewrite the PR title |

## How it works

1. On PR open, checks out the branch with full history
2. Runs `git diff <base>...HEAD` to get the full diff
3. Collects commit messages with `git log --oneline`
4. Sends both to GPT-4o with a structured prompt
5. Parses the JSON response and updates the PR body + labels via GitHub API
6. Skips update if the PR already has a substantial description (>100 chars)

## Why

Over 60% of PRs are merged with descriptions like "fix bug", "WIP", or nothing at all. This makes code review harder, changelogs meaningless, and git history useless for future maintainers.

`pr-pilot` takes 30 seconds to set up and runs on every PR automatically — no discipline required.

## Cost

Each PR description costs roughly **$0.01–0.03** with `gpt-4o` depending on diff size. For a team of 5 opening ~20 PRs/week, that's about **$1–3/month**.

## Self-hosted / private repos

Works with private repos. Just add the secret and the workflow file — no data leaves your GitHub Actions runner except the diff sent to OpenAI.

## Contributing

```bash
git clone https://github.com/albertusreza/pr-pilot
cd pr-pilot
pip install -e ".[dev]"
pytest
```

## License

MIT
