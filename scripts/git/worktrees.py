#!/usr/bin/env python3
r"""Git housekeeping across every repo under ~/GitHub and its worktrees in ~/GitHub/.worktrees.

    python scripts/git/worktrees.py sync                       # dry run, every repo
    python scripts/git/worktrees.py sync --repo workflows --apply
    python scripts/git/worktrees.py prune --apply              # remove merged worktrees and branches
    python scripts/git/worktrees.py prune --apply --remote     # also delete merged branches on origin

sync: fetches origin, fast-forwards each main checkout to origin/<default> (only if it is on the
default branch and clean) and merges origin/<default> into each clean worktree that is on a
branch. A merge that conflicts (or would overwrite untracked files) is aborted and reported, so no
worktree is left half-merged. Dirty trees, detached HEADs and already-merged worktrees are skipped
with the reason. Nothing is pushed.

prune: removes a worktree only when it is clean, on a branch, and its tip is merged (an ancestor
of origin/<default>, or the head of a merged GitHub PR, which catches squash merges; needs the
`gh` CLI, skipped quietly if missing); its local branch goes with it. Merged local branches
without a worktree are deleted too; unmerged branches never are. --remote also deletes merged
branches on origin ('git push origin --delete'); without it remote branches are never touched.
The default branch and HEAD are never deleted.

Both are dry runs unless --apply. Shared plumbing lives in scripts/common/gitrepos.py.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import gitrepos  # noqa: E402
from common.gitrepos import git  # noqa: E402


def sync_tree(repo, path, label, base, prs, apply, ff_only):
    """Merge origin/<base> into the tree at path. Returns a status word for the summary."""
    branch = git(path, "branch", "--show-current")[1]
    reason = gitrepos.skip_reason(path)
    if reason is None and ff_only and branch != base:
        reason = f"main checkout is on '{branch}', not {base}"
    if reason is None and not ff_only and gitrepos.is_merged(repo, git(path, "rev-parse", "HEAD")[1], base, prs, branch):
        reason = f"already merged into origin/{base} (worktrees.py prune)"
    if reason:
        print(f"  skip    {label} [{branch or '-'}]: {reason}")
        return "skipped"
    n = int(git(path, "rev-list", "--count", f"HEAD..origin/{base}")[1] or 0)
    if n == 0:
        print(f"  current {label} [{branch}]")
        return "current"
    if not apply:
        print(f"  update  {label} [{branch}]: {n} commit(s) behind (dry run)")
        return "updated"
    args = ["merge", "--ff-only", f"origin/{base}"] if ff_only else ["merge", "--no-edit", f"origin/{base}"]
    rc, _, err = git(path, *args)
    if rc == 0:
        print(f"  updated {label} [{branch}]: +{n} commit(s)")
        return "updated"
    git(path, "merge", "--abort")
    print(f"  FAILED  {label} [{branch}]: {err.splitlines()[0] if err else 'merge failed'} (aborted, left unchanged)")
    return "failed"


def prune_trees(repo, trees, base, prs, apply):
    """Remove clean worktrees whose branch is merged. Returns (removed, kept)."""
    removed = kept = 0
    for d in trees:
        branch = git(d, "branch", "--show-current")[1]
        reason = gitrepos.skip_reason(d)
        if reason is None and not gitrepos.is_merged(repo, git(d, "rev-parse", "HEAD")[1], base, prs, branch):
            reason = f"not merged into origin/{base}"
        if reason:
            kept += 1
            print(f"  keep    {d.name}  [{branch or '-'}]: {reason}")
            continue
        if not apply:
            removed += 1
            print(f"  remove  {d.name}  [{branch}]  (dry run)")
            continue
        rc, _, err = git(repo, "worktree", "remove", str(d))
        if rc != 0:
            kept += 1
            print(f"  FAILED  {d.name}: {err}")
            continue
        removed += 1
        # -d refuses unmerged work; squash-merged branches need -D (merge verified above).
        if git(repo, "branch", "-d", branch)[0] != 0:
            git(repo, "branch", "-D", branch)
        print(f"  removed {d.name}  [{branch}]")
    git(repo, "worktree", "prune")
    return removed, kept


def prune_branches(repo, base, prs, apply, remote):
    """Delete merged local (and optionally origin) branches that have no worktree."""
    in_use = {l[len("branch refs/heads/"):] for l in git(repo, "worktree", "list", "--porcelain")[1].splitlines()
              if l.startswith("branch refs/heads/")}
    deleted = 0

    def refs(prefix):
        out = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", prefix)[1]
        return [l.rsplit(" ", 1) for l in out.splitlines()]

    for name, oid in refs("refs/heads"):
        if name == base or name in in_use or not gitrepos.is_merged(repo, oid, base, prs, name):
            continue
        if not apply:
            deleted += 1
            print(f"  delete branch {name}  (dry run)")
        # -d refuses unmerged work; squash-merged branches need -D (merge verified above).
        elif git(repo, "branch", "-d", name)[0] == 0 or git(repo, "branch", "-D", name)[0] == 0:
            deleted += 1
            print(f"  deleted branch {name}")
        else:
            print(f"  FAILED  branch {name}")
    if remote:
        for full, oid in refs("refs/remotes/origin"):
            if full == "origin":  # the origin/HEAD symref, short-named "origin"; not a branch
                continue
            name = full.split("/", 1)[1] if "/" in full else full
            if name in ("HEAD", base) or not gitrepos.is_merged(repo, oid, base, prs, name):
                continue
            if not apply:
                deleted += 1
                print(f"  delete remote branch origin/{name}  (dry run)")
                continue
            rc, _, err = git(repo, "push", "origin", "--delete", name)
            if rc == 0:
                deleted += 1
            print(f"  deleted remote branch origin/{name}" if rc == 0 else f"  FAILED  origin/{name}: {err}")
    return deleted


def cmd_sync(args):
    totals = {"updated": 0, "current": 0, "skipped": 0, "failed": 0}
    for repo, trees in gitrepos.discover(args.github, args.root, args.repo).items():
        base = gitrepos.prepare(repo, not args.no_fetch)
        if not base:
            continue
        prs = gitrepos.merged_pr_heads(repo)
        work = [(repo, f"{repo.name} (main checkout)", True)] + [(d, d.name, False) for d in trees]
        for path, label, ff_only in work:
            totals[sync_tree(repo, path, label, base, prs, args.apply, ff_only)] += 1

    verb = "updated" if args.apply else "would update"
    print(f"\n{verb} {totals['updated']}, current {totals['current']}, skipped {totals['skipped']}, "
          f"failed {totals['failed']}" + ("" if args.apply else "  (re-run with --apply)"))
    return 1 if totals["failed"] else 0


def cmd_prune(args):
    removed = kept = branches = 0
    for repo, trees in gitrepos.discover(args.github, args.root, args.repo).items():
        base = gitrepos.prepare(repo, not args.no_fetch)
        if not base:
            continue
        prs = gitrepos.merged_pr_heads(repo)
        r, k = prune_trees(repo, trees, base, prs, args.apply)
        removed += r
        kept += k
        branches += prune_branches(repo, base, prs, args.apply, args.remote)

    verb = "removed" if args.apply else "would remove"
    print(f"\n{verb} {removed} worktree(s), kept {kept}; {verb} {branches} merged branch(es)"
          + ("" if args.apply else "  (re-run with --apply)"))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")
    s = sub.add_parser("sync", help="bring main checkouts and worktrees up to date with origin")
    gitrepos.add_common_args(s)
    s.set_defaults(func=cmd_sync)
    p = sub.add_parser("prune", help="remove merged worktrees and merged branches")
    gitrepos.add_common_args(p)
    p.add_argument("--remote", action="store_true", help="also delete merged branches on origin")
    p.set_defaults(func=cmd_prune)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
