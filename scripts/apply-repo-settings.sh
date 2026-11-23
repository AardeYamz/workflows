#!/usr/bin/env bash
# Applies .github/repo-settings.json to this repo and every consumer in bump-consumers.yml's matrix
# (or to the repos given as arguments, as `name` or `owner/name`). Needs `gh` logged in with admin on them.
# Repo settings can't be set from workflow YAML, so run this by hand after changing the JSON or adding a consumer.
set -euo pipefail
cd "$(dirname "$0")/.."
owner=AardeYamz

if [ $# -gt 0 ]; then
  repos=("$@")
else
  repos=(workflows)
  while read -r r; do repos+=("$r"); done < <(grep -oP '"repo": "\K[^"]+' .github/workflows/bump-consumers.yml)
fi

for r in "${repos[@]}"; do
  [[ $r == */* ]] || r="$owner/$r"
  if gh api -X PATCH "repos/$r" --input .github/repo-settings.json --silent; then
    echo "ok   $r"
  else
    echo "FAIL $r" >&2
    failed=1
  fi
done
exit "${failed:-0}"
