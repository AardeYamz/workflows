#!/usr/bin/env python3
r"""Bulk GitHub chores: Actions and PRs across the repos under ~/GitHub, and archiving old repos on GitHub.

    python scripts/github/repos.py builds                          # dry run: list the builds it would start
    python scripts/github/repos.py builds --repo trips,yl-theme --prs --apply
    python scripts/github/repos.py builds --workflow build.yml --apply
    python scripts/github/repos.py sync-prs                        # dry run: which PRs are behind, which conflict
    python scripts/github/repos.py sync-prs --repo 37-bestick-rd --apply
    python scripts/github/repos.py archive --older-than 730        # dry run: repos with no commit in 2 years
    python scripts/github/repos.py archive --match '^[23][0-9]{2}_' --apply

builds: starts each repo's build workflows on its default branch (and with --prs, on every open
same-repo PR branch too). A build is a workflow with a `pull_request` trigger; --workflow names
the ones to run instead (file name, e.g. build.yml, or the workflow's `name:`). A workflow with
`workflow_dispatch` is dispatched; one without it gets its latest run on that branch re-run. Tool
workflows (deploy tokens, pruning, releases, sync-prs) have no `pull_request` trigger and so are
never started unless named.

sync-prs: merges origin/<default> (main or master) into each open PR branch that is behind, and
pushes the merge. The merge runs in a throwaway worktree, so the checkout is never touched. Paths
the repo's sync-prs.yml caller lists as `ci-owned-paths` (version stamps, dist/) take the default
branch's copy, as that workflow does. A PR that conflicts is left alone and reported (the dry run
reports conflicts too). Fork and Dependabot PRs are skipped. A push made with your own token (not
GITHUB_TOKEN) starts the PR's pull_request workflows as usual.

archive: archives (makes read-only) repos on GitHub that --owner owns; local checkouts are not needed
or touched. Pick them with at least one of --older-than DAYS (last commit on the default branch; the push
date and other branches are ignored, since bulk maintenance branches renew them), --match REGEX (searched
in the name) and --repo; a repo must pass every one given. Branches with newer commits are listed, so
check them before --apply. --exclude names are never touched, and a repo with open issues or PRs is
skipped unless --include-open. Undo with `gh repo unarchive OWNER/NAME`.

builds and sync-prs only touch repos whose origin you own (your gh login, or --owner), so upstream
clones under ~/GitHub are left alone. All are dry runs unless --apply. Needs the `gh` CLI, logged in
with access to the repos (in CI, a token in GH_TOKEN). Shared plumbing lives in scripts/common/gitrepos.py.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import gitrepos  # noqa: E402
from common.gitrepos import git  # noqa: E402

BUILD_TRIGGER = "pull_request"
OWNED = """query($owner:String!,$endCursor:String){repositoryOwner(login:$owner){
repositories(first:50,after:$endCursor,ownerAffiliations:OWNER,isArchived:false){pageInfo{hasNextPage endCursor}
nodes{name createdAt issues(states:OPEN){totalCount} pullRequests(states:OPEN){totalCount}
defaultBranchRef{target{...on Commit{committedDate}}}
refs(refPrefix:"refs/heads/",first:100){nodes{name target{...on Commit{committedDate}}}}}}}}"""


def gh(*args):
    """(returncode, stdout, stderr-or-stdout) of gh args."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
    except OSError as e:
        return 127, "", str(e)
    return r.returncode, r.stdout.strip(), (r.stderr.strip() or r.stdout.strip())


def first_line(text, default="error"):
    return text.splitlines()[0] if text else default


def slug(repo):
    """owner/name of the repo's GitHub origin, or None."""
    url = git(repo, "remote", "get-url", "origin")[1]
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def login():
    rc, out, err = gh("api", "user", "--jq", ".login")
    if rc != 0 or not out:
        raise SystemExit(f"cannot tell who you are from gh ({first_line(err)}); pass --owner")
    return out


def targets(args):
    """Yield (checkout, default branch, owner/name) for each selected repo on GitHub owned by --owner,
    fetching it first. Repos owned by someone else (upstream clones) are skipped without a fetch."""
    owner = args.owner or login()
    others = 0
    for repo in gitrepos.discover(args.github, args.root, args.repo):
        name = slug(repo)
        if name and owner != "any" and name.split("/")[0].lower() != owner.lower():
            others += 1
            continue
        base = gitrepos.prepare(repo, not args.no_fetch)
        if base and not name:
            print("  origin is not on GitHub; skipping repo")
        if base and name:
            yield repo, base, name
    if others:
        print(f"\n({others} repo(s) not owned by {owner} skipped; --owner any includes them)")


def triggers(text):
    """The event names in a workflow file's `on:` (scalar, flow list, block list or block map)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"""^(?:on|'on'|"on")\s*:\s*(.*?)\s*(?:#.*)?$""", line)
        if not m:
            continue
        value = m.group(1)
        if value.startswith("["):
            return {s.strip().strip("'\"") for s in value.strip("[]").split(",") if s.strip()}
        if value:
            return {value.strip("'\"")}
        found, indent = set(), None
        for child in lines[i + 1:]:
            if not child.strip() or child.lstrip().startswith("#"):
                continue
            if not child[0].isspace():
                break
            width = len(child) - len(child.lstrip())
            indent = width if indent is None else indent
            if width != indent:
                continue
            k = re.match(r"""\s*(?:-\s*)?['"]?([A-Za-z_]+)['"]?\s*(?::|$)""", child)
            if k:
                found.add(k.group(1))
        return found
    return set()


def workflow_name(text, file):
    m = re.search(r"""^name:\s*['"]?(.*?)['"]?\s*(?:#.*)?$""", text, re.M)
    return m.group(1) if m else file


def workflows(repo, base):
    """[(file, name, triggers)] for the workflow files on origin/<base>."""
    out = git(repo, "ls-tree", "--name-only", f"origin/{base}", ".github/workflows/")[1]
    result = []
    for path in out.splitlines():
        file = path.rsplit("/", 1)[-1]
        if not file.endswith((".yml", ".yaml")):
            continue
        text = git(repo, "show", f"origin/{base}:{path}")[1]
        result.append((file, workflow_name(text, file), triggers(text)))
    return result


def open_prs(name):
    """[(number, branch, skip reason or None)] for the open PRs of owner/name."""
    rc, out, err = gh("pr", "list", "-R", name, "--state", "open", "--limit", "1000",
                      "--json", "number,headRefName,baseRefName,isCrossRepository,author")
    if rc != 0:
        raise RuntimeError(f"gh pr list: {first_line(err)}")
    prs = []
    for p in json.loads(out or "[]"):
        reason = None
        if p["isCrossRepository"]:
            reason = "from a fork"
        elif (p.get("author") or {}).get("login") in ("app/dependabot", "dependabot[bot]"):
            reason = "Dependabot (it rebases its own PRs)"
        prs.append((p["number"], p["headRefName"], p["baseRefName"], reason))
    return prs


def split_names(values):
    return [v for value in values or [] for v in re.split(r"[\s,]+", value) if v]


# ---------------------------------------------------------------------------------------------- builds

def start_build(name, file, label, ref, dispatchable, apply):
    """Dispatch `file` on `ref`, or re-run its latest run there. Returns a status word."""
    if dispatchable:
        if not apply:
            print(f"  run     {label} on {ref}  (dry run)")
            return "started"
        rc, _, err = gh("workflow", "run", file, "-R", name, "--ref", ref)
        print(f"  started {label} on {ref}" if rc == 0 else f"  FAILED  {label} on {ref}: {first_line(err)}")
        return "started" if rc == 0 else "failed"
    rc, out, err = gh("run", "list", "-R", name, "-w", file, "-b", ref, "-L", "1",
                      "--json", "databaseId,status")
    runs = json.loads(out or "[]") if rc == 0 else []
    if not runs:
        print(f"  skip    {label} on {ref}: no workflow_dispatch and no earlier run to re-run")
        return "skipped"
    run = runs[0]
    if run["status"] != "completed":
        print(f"  skip    {label} on {ref}: run {run['databaseId']} is still {run['status']}")
        return "skipped"
    if not apply:
        print(f"  re-run  {label} on {ref}: run {run['databaseId']}  (dry run)")
        return "started"
    rc, _, err = gh("run", "rerun", str(run["databaseId"]), "-R", name)
    print(f"  re-ran  {label} on {ref}: run {run['databaseId']}" if rc == 0
          else f"  FAILED  {label} on {ref}: {first_line(err)}")
    return "started" if rc == 0 else "failed"


def cmd_builds(args):
    wanted = set(split_names(args.workflow))
    totals = {"started": 0, "skipped": 0, "failed": 0}
    for repo, base, name in targets(args):
        picked = [(f, n, t) for f, n, t in workflows(repo, base)
                  if ((f in wanted or n in wanted) if wanted else BUILD_TRIGGER in t)]
        if not picked:
            print("  no matching workflows")
            continue
        refs = [base]
        if args.prs:
            try:
                prs = open_prs(name)
            except RuntimeError as e:
                print(f"  FAILED  {e}")
                totals["failed"] += 1
                prs = []
            refs += [branch for _, branch, _, reason in prs if not reason and branch != base]
        for file, wf_name, trig in picked:
            label = file if wf_name == file else f"{file} ({wf_name})"
            for ref in refs:
                totals[start_build(name, file, label, ref, "workflow_dispatch" in trig, args.apply)] += 1

    verb = "started" if args.apply else "would start"
    print(f"\n{verb} {totals['started']}, skipped {totals['skipped']}, failed {totals['failed']}"
          + ("" if args.apply else "  (re-run with --apply)"))
    return 1 if totals["failed"] else 0


# -------------------------------------------------------------------------------------------- sync-prs

def ci_owned_paths(repo, base):
    """The ci-owned-paths the repo's sync-prs.yml caller passes, as pathspecs."""
    text = git(repo, "show", f"origin/{base}:.github/workflows/sync-prs.yml")[1]
    m = re.search(r"""^\s*ci-owned-paths:\s*(.*?)\s*(?:#.*)?$""", text, re.M)
    return m.group(1).strip("'\"").split() if m else []


def sync_pr(repo, base, number, branch, ci_owned, apply):
    """Merge origin/<base> into the PR branch in a throwaway worktree. Returns a status word."""
    label = f"#{number} ({branch})"
    if git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}")[0] != 0:
        # Not fetched yet (--no-fetch on an old checkout); fetch just this branch.
        rc, _, err = git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        if rc != 0:
            print(f"  FAILED  {label}: fetch: {first_line(err)}")
            return "failed"
    tip = git(repo, "rev-parse", f"refs/remotes/origin/{branch}")[1]
    if git(repo, "merge-base", "--is-ancestor", f"origin/{base}", tip)[0] == 0:
        print(f"  current {label}")
        return "current"
    behind = git(repo, "rev-list", "--count", f"{tip}..origin/{base}")[1]
    tmp = tempfile.mkdtemp(prefix="sync-prs-")
    tree = os.path.join(tmp, "wt")
    try:
        rc, _, err = git(repo, "worktree", "add", "--detach", "--quiet", tree, tip)
        if rc != 0:
            print(f"  FAILED  {label}: worktree: {first_line(err)}")
            return "failed"
        git(tree, "merge", "--no-commit", "--no-ff", f"origin/{base}")
        for p in ci_owned:
            git(tree, "checkout", f"origin/{base}", "--", p)
        conflicts = git(tree, "diff", "--name-only", "--diff-filter=U")[1].split()
        if conflicts:
            shown = ", ".join(conflicts[:5]) + (" ..." if len(conflicts) > 5 else "")
            print(f"  CONFLICT {label}: {shown}; not updated, merge {base} in by hand")
            return "conflict"
        if not apply:
            print(f"  update  {label}: {behind} commit(s) behind {base}, merges cleanly  (dry run)")
            return "updated"
        rc, _, err = git(tree, "commit", "--no-edit", "-q", "-m", f"Merge {base} into {branch}")
        if rc != 0:
            print(f"  FAILED  {label}: commit: {first_line(err)}")
            return "failed"
        rc, _, err = git(tree, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
        if rc != 0:
            print(f"  FAILED  {label}: push rejected: {first_line(err)}")
            return "failed"
        print(f"  updated {label}: +{behind} commit(s) from {base}")
        return "updated"
    finally:
        git(repo, "worktree", "remove", "--force", tree)
        git(repo, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_sync_prs(args):
    totals = {"updated": 0, "current": 0, "skipped": 0, "conflict": 0, "failed": 0}
    for repo, base, name in targets(args):
        try:
            prs = open_prs(name)
        except RuntimeError as e:
            print(f"  FAILED  {e}")
            totals["failed"] += 1
            continue
        prs = [p for p in prs if p[2] == base]
        if not prs:
            print(f"  no open PRs into {base}")
            continue
        ci_owned = ci_owned_paths(repo, base)
        for number, branch, _, reason in prs:
            if reason:
                print(f"  skip    #{number} ({branch}): {reason}")
                totals["skipped"] += 1
                continue
            totals[sync_pr(repo, base, number, branch, ci_owned, args.apply)] += 1

    verb = "updated" if args.apply else "would update"
    print(f"\n{verb} {totals['updated']}, current {totals['current']}, skipped {totals['skipped']}, "
          f"conflicts {totals['conflict']}, failed {totals['failed']}"
          + ("" if args.apply else "  (re-run with --apply)"))
    return 1 if totals["failed"] or totals["conflict"] else 0


# --------------------------------------------------------------------------------------------- archive

def when(stamp):
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def last_commit(repo):
    """When the default branch was last committed to; the creation time for an empty repo."""
    head = repo["defaultBranchRef"]
    # committedDate keeps the committer's offset: compare these as instants, not strings.
    return when(head["target"]["committedDate"] if head else repo["createdAt"])


def newer_branches(repo):
    """Names of the branches (first 100) with a commit newer than the default branch's."""
    date = last_commit(repo)
    return [n["name"] for n in repo["refs"]["nodes"] if when(n["target"]["committedDate"]) > date]


def archive_plan(repos, today, older_than=None, match=None, only=(), exclude=(), include_open=False):
    """[(repo name, last commit date, newer branches, skip reason or None)] for the unarchived repos the
    filters select."""
    only = {n.lower() for n in only}
    exclude = {n.lower() for n in exclude}
    plan = []
    for r in sorted(repos, key=lambda r: r["name"].lower()):
        name = r["name"]
        if (only and name.lower() not in only) or (match and not match.search(name)):
            continue
        date = last_commit(r).astimezone(datetime.timezone.utc).date()
        if older_than is not None and (today - date).days < older_than:
            continue
        open_count = r["issues"]["totalCount"] + r["pullRequests"]["totalCount"]
        reason = None
        if name.lower() in exclude:
            reason = "excluded"
        elif open_count and not include_open:
            reason = f"{open_count} open issue(s) or PR(s); --include-open archives it anyway"
        plan.append((name, date, newer_branches(r), reason))
    return plan


def owned_repos(owner):
    """Every unarchived repo `owner` owns that the gh token can see."""
    rc, out, err = gh("api", "graphql", "--paginate", "-f", "query=" + OWNED, "-f", "owner=" + owner,
                      "--jq", ".data.repositoryOwner.repositories.nodes[]")
    if rc != 0:
        raise SystemExit(f"cannot list the repos of {owner}: {first_line(err)}")
    return [json.loads(line) for line in out.splitlines()]


def cmd_archive(args):
    only, exclude = split_names(args.repo), split_names(args.exclude)
    if args.older_than is None and not args.match and not only:
        raise SystemExit("archive needs --older-than, --match or --repo; it never selects every repo by default")
    owner = args.owner or login()
    repos = owned_repos(owner)
    known = {r["name"].lower() for r in repos}
    for name in only:
        if name.lower() not in known:
            print(f"? {name}: not an unarchived repo of {owner} (skipped)")
    done = skipped = failed = 0
    today = datetime.datetime.now(datetime.timezone.utc).date()
    for name, date, newer, reason in archive_plan(repos, today, args.older_than, args.match, only, exclude,
                                                  args.include_open):
        label = f"{owner}/{name} (last commit {date}" + (f"; newer on {', '.join(newer)}" if newer else "") + ")"
        if reason:
            print(f"  skip     {label}: {reason}")
            skipped += 1
        elif not args.apply:
            print(f"  archive  {label}  (dry run)")
            done += 1
        else:
            rc, _, err = gh("repo", "archive", f"{owner}/{name}", "--yes")
            print(f"  archived {label}" if rc == 0 else f"  FAILED   {label}: {first_line(err)}")
            done += rc == 0
            failed += rc != 0
    verb = "archived" if args.apply else "would archive"
    print(f"\n{verb} {done}, skipped {skipped}, failed {failed}"
          + (f"  (undo: gh repo unarchive {owner}/NAME)" if args.apply else "  (re-run with --apply)"))
    return 1 if failed else 0


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def add_args(ap):
    gitrepos.add_common_args(ap)
    ap.add_argument("--owner", help="only repos whose origin this GitHub user or org owns (default: your gh login; "
                                    "'any' for all)")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")
    b = sub.add_parser("builds", help="start the build workflows on the default branch (and open PRs)")
    add_args(b)
    b.add_argument("--workflow", action="append", metavar="FILE",
                   help="run these workflows (file or name, comma separated or repeated) instead of every build")
    b.add_argument("--prs", action="store_true", help="also run them on every open same-repo PR branch")
    b.set_defaults(func=cmd_builds)
    s = sub.add_parser("sync-prs", help="merge the default branch into every open PR that is behind")
    add_args(s)
    s.set_defaults(func=cmd_sync_prs)
    a = sub.add_parser("archive", help="archive old or matching repos on GitHub")
    a.add_argument("--older-than", type=positive_int, metavar="DAYS", help="no commit on the default branch for DAYS days")
    a.add_argument("--match", type=re.compile, metavar="REGEX", help="repo names this regex is found in")
    a.add_argument("--repo", action="append", metavar="NAME", help="only these repos (comma separated or repeated)")
    a.add_argument("--exclude", action="append", metavar="NAME", help="never these repos (comma separated or repeated)")
    a.add_argument("--include-open", action="store_true", help="also archive repos with open issues or PRs")
    a.add_argument("--owner", help="user or organisation whose repos to archive (default: your gh login)")
    a.add_argument("--apply", action="store_true", help="archive; without it, only list")
    a.set_defaults(func=cmd_archive)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
