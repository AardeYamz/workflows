#!/usr/bin/env python3
r"""Find what still names a renamed repo, across every checkout under ~/GitHub.

    python scripts/git/rename_check.py personal-dns
    python scripts/git/rename_check.py personal-dns personal-tailnet --no-fetch

For each old name it looks for AardeYamz/<old>, ../<old>, GitHub/<old> (either slash) and vendor/<old>, in file
contents and in tracked paths, on each repo's origin default branch. A longer name (personal-dns-acl) does not count.
It also reads ~/GitHub/*.code-workspace and ~/.claude/CLAUDE.md. Hits are listed by repo, code first, then docs
(Markdown and docs/). Read only. Exits 1 if code still names an old repo.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import gitrepos  # noqa: E402
from common.gitrepos import git  # noqa: E402


def pattern(owner, olds):
    """An extended regular expression (read the same by git grep -E and re) for every way a file names the repos."""
    prefix = r"(%s/|\.\.[/\\]|GitHub[/\\]|vendor/)" % re.escape(owner)
    names = "|".join(re.escape(o) for o in olds)
    return prefix + "(" + names + r")($|[^A-Za-z0-9_-])"


def is_doc(path):
    parts = path.split("/")
    return path.lower().endswith(".md") or "docs" in parts[:-1]


def repo_hits(repo, ref, regex):
    """[(path, "path:line: text")] for one repo at ref: matching lines, then matching tracked paths."""
    rc, out, err = git(repo, "grep", "-nIiE", "-z", regex, ref, "--")
    if rc not in (0, 1):
        raise RuntimeError(err)
    hits = []
    for line in out.split("\n") if rc == 0 else []:
        name, number, text = line.split("\0", 2)
        path = name[len(ref) + 1:]
        hits.append((path, f"{path}:{number}: {text.strip()[:160]}"))
    tracked = git(repo, "ls-tree", "-r", "--name-only", "-z", ref)[1].split("\0")
    hits += [(p, f"{p}  (path)") for p in tracked if p and re.search(regex, p, re.I)]
    return hits


def report(label, hits):
    code = [h for p, h in hits if not is_doc(p)]
    docs = [h for p, h in hits if is_doc(p)]
    print(f"\n== {label}: {len(code)} in code, {len(docs)} in docs")
    for kind, lines in (("code", code), ("docs", docs)):
        for line in lines:
            print(f"  {kind}  {line}")
    return len(code), len(docs)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old", nargs="+", metavar="OLD_NAME", help="old repo (or folder) names")
    ap.add_argument("--owner", default="AardeYamz")
    ap.add_argument("--github", type=Path, default=gitrepos.GITHUB, help=f"folder of repo checkouts (default {gitrepos.GITHUB})")
    ap.add_argument("--claude-md", type=Path, default=Path.home() / ".claude" / "CLAUDE.md")
    ap.add_argument("--no-fetch", action="store_true", help="skip 'git fetch origin --prune'")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    regex = pattern(a.owner, a.old)
    code = docs = 0
    failed = False
    for repo in gitrepos.discover(a.github, a.github / ".worktrees", None):
        if not a.no_fetch and git(repo, "remote", "get-url", "origin")[0] == 0:
            if git(repo, "fetch", "origin", "--prune", "--quiet")[0] != 0:
                print(f"? {repo.name}: fetch failed, searching what was fetched before")
        base = gitrepos.default_branch(repo)
        ref = f"origin/{base}" if base else "HEAD"
        if git(repo, "rev-parse", "--verify", "-q", ref)[0] != 0:
            continue  # no commits
        try:
            hits = repo_hits(repo, ref, regex)
        except RuntimeError as e:
            print(f"? {repo.name}: {e}")
            failed = True
            continue
        if hits:
            c, d = report(f"{repo.name} ({ref})", hits)
            code, docs = code + c, docs + d
    # Not in any repo. Workspace files list folders as bare relative paths, so they are matched on their own.
    bare = r'"path"\s*:\s*"(\./)?(%s)["/\\]' % "|".join(re.escape(o) for o in a.old)
    loose = [(f, f"{regex}|{bare}") for f in sorted(a.github.glob("*.code-workspace"))] + [(a.claude_md, regex)]
    for f, wanted in loose:
        if not f.is_file():
            continue
        hits = [(f.name, f"{f.name}:{n}: {line.strip()[:160]}")
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                if re.search(wanted, line, re.I)]
        if hits:
            # a workspace or the global instructions act on the name, so they count as code
            for _, line in hits:
                print(f"\n== {f}\n  code  {line}")
            code += len(hits)
    print(f"\n{code} hit(s) in code, {docs} in docs")
    return 1 if code or failed else 0


if __name__ == "__main__":
    sys.exit(main())
