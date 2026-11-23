r"""Shared plumbing for the git housekeeping scripts (scripts/git/worktrees.py).

Both walk every repo under ~/GitHub plus the linked worktrees in ~/GitHub/.worktrees, fetch
origin, and decide per tree from the same facts (default branch, clean or dirty, merged or
not). That walk and those facts live here so each script only holds its own action.

Import from a script in scripts/<folder>/:

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from common import gitrepos
"""
import json
import subprocess
from pathlib import Path

GITHUB = Path.home() / "GitHub"
WORKTREES = GITHUB / ".worktrees"


def git(cwd, *args):
    """(returncode, stdout, stderr-or-stdout) of git -C cwd args."""
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), (r.stderr.strip() or r.stdout.strip())


def add_common_args(ap):
    """The flags every git housekeeping script shares."""
    ap.add_argument("--github", type=Path, default=GITHUB, help=f"folder of repo checkouts (default {GITHUB})")
    ap.add_argument("--root", type=Path, default=WORKTREES, help=f"worktrees folder (default {WORKTREES})")
    ap.add_argument("--repo", help="only this repo (folder name of the main checkout)")
    ap.add_argument("--apply", action="store_true", help="act (default is a dry run)")
    ap.add_argument("--no-fetch", action="store_true", help="skip 'git fetch origin --prune'")


def linked_worktrees(repo, root):
    """Linked worktrees of repo (not the main checkout) that live under root."""
    out = git(repo, "worktree", "list", "--porcelain")[1]
    paths = [Path(l[9:]) for l in out.splitlines() if l.startswith("worktree ")]
    root = root.resolve()
    return [p for p in paths[1:] if p.exists() and root in p.resolve().parents]


def discover(github, root, only=None):
    """{main checkout: [linked worktrees under root]}, covering every checkout under github
    and any repo that owns a worktree in root."""
    repos = {}
    if github.is_dir():
        for p in sorted(github.iterdir()):
            if (p / ".git").is_dir() and (not only or p.name == only):
                repos[p] = linked_worktrees(p, root)
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if (p / ".git").is_file()):
            rc, common, _ = git(d, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if rc != 0:
                print(f"? {d.name}: not a valid worktree (skipped)")
                continue
            repo = Path(common).parent
            if (not only or repo.name == only) and d not in repos.setdefault(repo, []):
                repos[repo].append(d)
    return repos


def default_branch(repo):
    rc, out, _ = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out:
        return out.split("/", 1)[1]
    for name in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}")[0] == 0:
            return name
    return None


def prepare(repo, fetch):
    """Print the repo header, fetch, and return its origin default branch (None = skip repo)."""
    print(f"\n== {repo.name}")
    if git(repo, "remote", "get-url", "origin")[0] != 0:
        print("  no origin remote; skipping repo")
        return None
    if fetch:
        rc, _, err = git(repo, "fetch", "origin", "--prune")
        if rc != 0:
            print(f"  FAILED  fetch: {err.splitlines()[0] if err else 'error'}")
            return None
    base = default_branch(repo)
    if not base:
        print("  no origin default branch found; skipping repo")
    return base


def merged_pr_heads(repo):
    """{headRefName: headRefOid} for merged PRs, via gh. Empty if gh is unavailable."""
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--limit", "200",
             "--json", "headRefName,headRefOid"],
            cwd=repo, capture_output=True, text=True)
        if r.returncode == 0:
            return {p["headRefName"]: p["headRefOid"] for p in json.loads(r.stdout)}
    except (OSError, ValueError):
        pass
    return {}


def is_merged(repo, oid, base, prs, name):
    """oid is an ancestor of origin/<base>, or is the tip of a merged PR from branch `name`
    (catches squash merges and PRs merged into another branch)."""
    return git(repo, "merge-base", "--is-ancestor", oid, f"origin/{base}")[0] == 0 or prs.get(name) == oid


def skip_reason(path):
    """Why a tree must not be touched: detached HEAD or uncommitted changes. None if fine."""
    if not git(path, "branch", "--show-current")[1]:
        return "detached HEAD"
    if git(path, "status", "--porcelain")[1]:
        return "uncommitted changes"
    return None
