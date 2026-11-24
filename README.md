# AresStack Shared Workflows

Central advisory Gemini 4 Neon code reviews via the Gemini Developer API Flex tier.

## Setup

Create the organization Actions secret `GEMINI_API_KEY` and enable paid API billing.
Grant access to each participating repository, subject to the organization's GitHub plan.
Do not inherit unrelated secrets. The workflow uses the ephemeral GitHub token.

## Reusable workflow

Add this file to a participating repository under `.github/workflows/gemini-review.yml`:

```yaml
name: Gemini Flex PR Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    uses: aresstack/shared-workflows/.github/workflows/gemini-review.yml@main
    secrets:
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

Protect the central main branch; pin a commit SHA instead of main if immutable releases
are preferred. The workflow checks out its own workflow revision using the job context.

For enterprise organization rulesets, the same workflow also supports a direct
pull_request trigger. Configure rulesets separately; this repository does not
activate them. A merge queue is not yet supported.

## Behavior and security

- Read the PR diff via GitHub REST; never check out or execute target PR code.
- Review same-repository PRs only. Skip forks and Dependabot; normal pull_request
  workflows do not have their secrets for these sources.
- Send the diff to Google for analysis. Verify that sending private source code is
  authorized under the applicable account and data-handling policy.
- Use service_tier=flex exclusively, 900000 ms API timeout, 45-minute job timeout.
- Retry 429 or 503 once after 30 seconds; never switch to standard.
- Use medium thinking effort; limit output to 16384 tokens per request.
- Reject diffs larger than 400000 bytes rather than silently truncate.
- Cancel superseded runs and verify base/head SHAs before reading and publishing.
  Cancellation does not guarantee cancellation or non-billing of an in-flight API call.
- Publish a COMMENT review with file/line references in its body; no inline annotations,
  code writes, approvals, or requests for changes. API failures fail the job.
- The first version analyzes only the diff, not the entire repository or linked issues.
- Direct and reusable modes should not both be enabled for the same target PR.

## Validation

`python -m unittest discover -s tests -v`

Unit tests use mocks and do not require API credentials. A live PR run is necessary
to verify the actual API and GitHub permissions.
