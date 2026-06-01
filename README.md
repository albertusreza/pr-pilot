# pr-pilot ✈️

**Stop writing PR descriptions.** Let AI do it.

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
pip install pr-pilot
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
