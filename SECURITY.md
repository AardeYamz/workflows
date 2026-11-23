# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting: **Security → Report a vulnerability** on this repository ([direct link](https://github.com/AardeYamz/workflows/security/advisories/new)). Include what you found, how to reproduce it, and what it affects. Expect a first reply within a week.

## Scope

- `.github/workflows/`: the reusable workflows other AardeYamz repos call, and this repo's own CI.
- `mermaid-lint/` and `d2-lint/`: the diagram linters and their dependencies.

## Supported versions

Only the latest commit on `main` (and the moving major tags pointing at it) is supported.
