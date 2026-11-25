#!/usr/bin/env python3
r"""Bulk chores on GitHub Projects (v2) boards.

    python scripts/github/projects.py archive-closed                  # dry run, every project you own
    python scripts/github/projects.py archive-closed --project 2,5 --apply
    python scripts/github/projects.py archive-closed --owner SomeOrg --include-prs --apply

archive-closed: archives every board item whose issue is closed. The test is the issue's state, not
the board's Status column, so an open issue marked Done is kept. Pull requests are left alone unless
--include-prs, which also archives closed and merged ones. Draft items and open issues are never touched. Archived items leave the board but are not deleted;
restore them from the project's Archive view.

--owner defaults to your gh login; --project narrows to project numbers. A dry run unless --apply.
Needs the `gh` CLI, logged in with the `project` scope (gh auth refresh -s project).
"""
import argparse
import subprocess
import sys

ITEMS = """query($p:ID!,$endCursor:String){node(id:$p){...on ProjectV2{items(first:100,after:$endCursor){
pageInfo{hasNextPage endCursor} nodes{id isArchived content{__typename
...on Issue{number state repository{nameWithOwner}} ...on PullRequest{number state repository{nameWithOwner}}}}}}}}"""
CLOSED = ("CLOSED", "MERGED")


def gh(*args, timeout=60):
    """(returncode, stdout, stderr-or-stdout) of gh args."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout.strip(), (r.stderr.strip() or r.stdout.strip())


def login():
    code, out, err = gh("api", "user", "--jq", ".login")
    if code:
        sys.exit("gh is not logged in: " + err)
    return out


def projects(owner, only):
    code, out, err = gh("project", "list", "--owner", owner, "--limit", "100", "--format", "json",
                        "--jq", '.projects[] | "\\(.number)\\t\\(.id)\\t\\(.title)"')
    if code:
        sys.exit("cannot list projects (is the project scope granted?): " + err)
    rows = [line.split("\t", 2) for line in out.splitlines()]
    return [(int(n), pid, title) for n, pid, title in rows if not only or int(n) in only]


def closed_items(project_id, include_prs):
    """[(item id, 'owner/repo#n')] for every unarchived item whose issue (and with include_prs, PR) is closed."""
    kinds = '(.content.__typename=="Issue" or .content.__typename=="PullRequest")' if include_prs else '.content.__typename=="Issue"'
    code, out, err = gh("api", "graphql", "--paginate", "-f", "query=" + ITEMS, "-f", "p=" + project_id,
                        "--jq", ".data.node.items.nodes[] | select(.isArchived==false and " + kinds + ' and (.content.state=="CLOSED" or .content.state=="MERGED"))'
                        ' | "\\(.id)\\t\\(.content.repository.nameWithOwner)#\\(.content.number)"', timeout=300)
    if code:
        raise RuntimeError(first_line(err))
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]


def first_line(text):
    return text.splitlines()[0] if text else "error"


def cmd_archive_closed(args):
    owner = args.owner or login()
    only = {int(n) for n in args.project.split(",")} if args.project else set()
    failed = 0
    for number, pid, title in projects(owner, only):
        try:
            items = closed_items(pid, args.include_prs)
        except RuntimeError as e:
            print(f"project {number} ({title}): cannot read items: {e}")
            failed += 1
            continue
        print(f"project {number} ({title}): {len(items)} closed item(s)")
        for item_id, ref in items:
            if not args.apply:
                print(f"  would archive {ref}")
                continue
            code, _, err = gh("project", "item-archive", str(number), "--owner", owner, "--id", item_id)
            print(f"  archived {ref}" if not code else f"  FAILED {ref}: {first_line(err)}")
            failed += bool(code)
    if not args.apply:
        print("dry run; add --apply to archive")
    return 1 if failed else 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    a = sub.add_parser("archive-closed", help="archive every board item whose issue is closed")
    a.add_argument("--owner", help="user or organisation that owns the projects (default: your gh login)")
    a.add_argument("--project", help="comma-separated project numbers (default: all)")
    a.add_argument("--include-prs", action="store_true", help="also archive closed and merged pull requests")
    a.add_argument("--apply", action="store_true", help="archive; without it, only list")
    a.set_defaults(func=cmd_archive_closed)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
