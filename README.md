# workflows

Shared GitHub Actions for AardeYamz repos: reusable workflows and the diagram linters they run. Each repo keeps a short caller workflow with its own triggers, concurrency and permissions, and `uses:` a template from here pinned by SHA, so CI logic and action bumps live in one place.

This repo is public so that public repos can call it (GitHub doesn't let a public repo use a private repo's workflows or actions). Keep it free of secrets, hostnames and anything else repo-specific: those belong in the callers.

## Reusable workflows

| Workflow | Inputs | What it does |
|---|---|---|
| [`diagram-lint.yml`](.github/workflows/diagram-lint.yml) | `mermaid` (default `true`), `d2` (default `false`) | Runs [`mermaid-lint`](mermaid-lint/) and/or [`d2-lint`](d2-lint/) on the caller's repo |
| [`zizmor.yml`](.github/workflows/zizmor.yml) | `advanced-security` (default `true`) | Lints the caller's workflow files with [zizmor](https://docs.zizmor.sh) and uploads SARIF to code scanning. Private repos without code scanning pass `false` to get annotations and a failing job instead |
| [`sync-prs.yml`](.github/workflows/sync-prs.yml) | `base`, `ci-owned-paths`, `dispatch-workflows`, `dispatch-on-base` | Merges the base branch into each open, behind, same-repo PR. Comments on conflicts, skips Dependabot PRs and merges that would change `.github/workflows/**`, and can re-run workflows on each updated branch or once on the base branch |
| [`dependabot-auto-merge.yml`](.github/workflows/dependabot-auto-merge.yml) | `required-workflow`, `merge-method`, `timeout-minutes` | Waits for a Dependabot PR's checks, then merges patch/minor bumps and dev-only majors |
| [`codeql.yml`](.github/workflows/codeql.yml) | `languages` (default `javascript-typescript,actions`) | CodeQL analysis of the caller's code, uploaded to code scanning |
| [`dependency-review.yml`](.github/workflows/dependency-review.yml) | `fail-on-severity` (default `moderate`) | Fails a pull request that adds a vulnerable dependency |

Every workflow also takes `runs-on`. Left empty, it uses the caller's `vars.RUNNER`, else `ubuntu-26.04`. Each file's header comment includes a sample caller and the permissions the caller must grant. A called job can't ask for more permissions than its caller grants, so grant everything listed.

```yaml
jobs:
  diagrams:
    uses: AardeYamz/workflows/.github/workflows/diagram-lint.yml@<sha> # v1.0.0
    with:
      d2: true
```

## Security and branch protection

- [`scorecard.yml`](.github/workflows/scorecard.yml) runs OpenSSF Scorecard on this repo weekly and on `main`, in this repo only (Scorecard won't publish from a reusable workflow).
- [`.github/rulesets/default-branch-protection.json`](.github/rulesets/default-branch-protection.json) is the default-branch ruleset: no deletion or force-push, PRs required with resolved threads, strict required checks (self-test, zizmor, CodeQL, dependency review) and a code scanning gate. Repo admins can bypass through a PR only. Apply or update it with:

```
gh api repos/AardeYamz/workflows/rulesets --method POST --input .github/rulesets/default-branch-protection.json
gh api repos/AardeYamz/workflows/rulesets/<id> --method PUT --input .github/rulesets/default-branch-protection.json
```

Secret scanning and push protection are enabled in the repo settings.

## Diagram linters

Both are composite actions as well: `uses: AardeYamz/workflows/mermaid-lint@<sha>` (or `d2-lint`), after `actions/checkout`.

- **`mermaid-lint/`** parses every ```mermaid block in the repo's tracked `*.md` files with `mermaid.parse` (jsdom supplies the DOM).
- **`d2-lint/`** compiles every tracked `*.d2` file and every ```d2 block with [D2.js](https://www.npmjs.com/package/@terrastruct/d2), the official WASM build of d2, so no binary is needed. Compiling includes the layout, so it also catches unknown keywords and bad references. `@import`s between tracked `.d2` files resolve.

Each prints `file:line: message` for every broken diagram, writes `::error` annotations under Actions, and exits 1 if any fail. To run them locally from the repo whose diagrams you edited, with this repo cloned next to it:

```
npm ci --prefix ../workflows/mermaid-lint && node ../workflows/mermaid-lint/lint.mjs
npm ci --prefix ../workflows/d2-lint && node ../workflows/d2-lint/lint.mjs
```

The linters check the source, not any rendered SVG.

## Releasing

1. Change things on a branch and open a PR. [`selftest.yml`](.github/workflows/selftest.yml) runs the linters from source, checks they reject broken diagrams, and calls the read-only templates at the PR's commit.
2. When a linter changes, bump the SHA that `diagram-lint.yml` pins to the linter's new commit, in a follow-up PR.
3. After merging, tag the merge commit `vX.Y.Z`, create a release, and move the `v1` tag. Callers then bump to the new tag's SHA, with `# vX.Y.Z` as the comment.

Dependabot bumps the actions pinned here and the linters' npm dependencies weekly.

## Licence

[MIT](LICENSE)
