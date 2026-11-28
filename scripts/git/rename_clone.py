#!/usr/bin/env python3
r"""Rename one repo's clone on this machine, after (or before) renaming the repo on GitHub.

    python scripts/git/rename_clone.py OLD_FOLDER NEW_NAME             # dry run
    python scripts/git/rename_clone.py OLD_FOLDER NEW_NAME --apply

OLD_FOLDER is the folder of the main checkout under ~/GitHub; NEW_NAME is the new folder and repo name. It
  1. renames the folder,
  2. points origin at the new repo name, keeping the owner and the URL form (HTTPS or SSH),
  3. runs 'git worktree repair', so linked worktrees find the moved checkout (worktree folders keep their names),
  4. moves the Claude Code project folders (memory and sessions) in ~/.claude/projects,
  5. rewrites the folder's "path" entries in ~/GitHub/*.code-workspace,
and then lists what is left to do by hand. Dry run unless --apply. It never touches GitHub.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import gitrepos  # noqa: E402
from common.gitrepos import git  # noqa: E402


def encode(path):
    """The ~/.claude/projects folder name Claude Code uses for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def new_url(url, name):
    """url with its last path segment (the repo name) replaced."""
    m = re.match(r"^(.*[/:])([^/:]+?)(\.git)?/?$", url)
    return m.group(1) + name + (m.group(3) or "") if m else None


def claude_folders(projects, github, old, new):
    """(moves, unsure): the project folders of the old checkout as (from, to) pairs, and the names that only look
    like they belong to a folder inside it. The encoding turns '/', '.' and '-' into '-', so 'repo-x' may be a
    subfolder 'repo/x' or a sibling repo 'repo-x': only the checkout itself and its dot folders are moved."""
    if not projects.is_dir():
        return [], []
    src, dst = encode(github / old), encode(github / new)
    siblings = [encode(p).lower() for p in github.iterdir() if p.is_dir() and p.name != old]
    moves, unsure = [], []
    for d in sorted(p for p in projects.iterdir() if p.is_dir()):
        low, rest = d.name.lower(), d.name[len(src):]
        if low != src.lower() and not low.startswith(src.lower() + "-"):
            continue
        if any(len(s) > len(src) and (low == s or low.startswith(s + "-")) for s in siblings):
            continue  # belongs to a sibling folder with a longer name
        if rest and not rest.startswith("--"):
            unsure.append(d.name)
            continue
        # keep the source folder's first letter: on Windows it is the drive letter, in either case
        moves.append((d, projects / (d.name[0] + dst[1:] + rest)))
    return moves, unsure


def merge_move(src, dst):
    """Move src to dst. If dst exists, move what is not in it yet; returns the files left behind."""
    if not dst.exists():
        src.rename(dst)
        return []
    left = []
    for item in sorted(src.iterdir()):
        target = dst / item.name
        if item.is_dir() and target.is_dir():
            left += merge_move(item, target)
        elif target.exists():
            left.append(item)
        else:
            item.rename(target)
    if not any(src.iterdir()):
        src.rmdir()
    return left


def rewrite_workspace(text, github, old, new):
    """text with every "path" that names the old checkout (relative or absolute) pointing at the new one."""
    parent = re.escape(github.as_posix()).replace("/", r"(?:/|\\\\)")
    pattern = r'("path"\s*:\s*"(?:\./|%s(?:/|\\\\))?)%s(?=["/\\])' % (parent, re.escape(old))
    return re.subn(pattern, lambda m: m.group(1) + new, text)


def moved_path(path, src, dst):
    """path, or its place under dst when it was inside src."""
    return dst / path.relative_to(src) if path == src or src in path.parents else path


def rename(github, projects, old, new, apply):
    src, dst = github / old, github / new
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", new) or new in (".", ".."):
        sys.exit(f"not a repo name: {new}")
    if not (src / ".git").is_dir():
        sys.exit(f"{src} is not a main checkout")
    if old != new and dst.exists() and not dst.samefile(src):
        sys.exit(f"{dst} already exists: move it out of the way first")
    if Path.cwd() == src or src in Path.cwd().parents:
        sys.exit(f"run this from outside {src}")
    rc, url, _ = git(src, "remote", "get-url", "origin")
    target = new_url(url, new) if rc == 0 else None
    out = git(src, "worktree", "list", "--porcelain")[1]
    trees = [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")][1:]
    moves, unsure = claude_folders(projects, github, old, new)
    workspaces = []
    for f in sorted(github.glob("*.code-workspace")):
        text, n = rewrite_workspace(f.read_text(encoding="utf-8"), github, old, new)
        if n and old != new:
            workspaces.append((f, text, n))

    verb = "" if apply else "would "
    if old != new:
        print(f"{verb}rename   {src} -> {dst}")
    if target and target != url:
        print(f"{verb}set-url  origin {url} -> {target}")
    for t in trees:
        print(f"{verb}repair   worktree {moved_path(t, src.resolve(), dst.resolve())}")
    for a, b in moves:
        if a != b:
            print(f"{verb}move     {a} -> {b}" + ("  (merging into the existing folder)" if b.exists() else ""))
    for f, _, n in workspaces:
        print(f"{verb}rewrite  {f.name}: {n} path(s)")

    failed = False
    if apply:
        real_src = src.resolve()
        if old != new:
            src.rename(dst)
        if target and target != url:
            rc, _, err = git(dst, "remote", "set-url", "origin", target)
            if rc != 0:
                print(f"FAILED   set-url: {err}")
                failed = True
        if trees:
            # worktrees inside the checkout moved with it, so they are given at their new place
            paths = [str(p) for p in (moved_path(t, real_src, dst.resolve()) for t in trees) if p.exists()]
            rc, _, err = git(dst, "worktree", "repair", *paths)
            if rc != 0:
                print(f"FAILED   worktree repair: {err}")
                failed = True
        for a, b in moves:
            if a != b:
                for f in merge_move(a, b):
                    print(f"kept     {f}: a file of that name is already in {b}")
                    failed = True
        for f, text, _ in workspaces:
            f.write_text(text, encoding="utf-8")

    print("\nBy hand:")
    for name in unsure:
        print(f"  - {projects / name}: move it if it belongs to a folder inside {old}")
    print("  - Claude Code asks to trust the new path the first time: accept it")
    print("  - GitHub Desktop (Windows): scripts/github-desktop/relocate.mjs github-desktop --from OLD --to NEW")
    print(f"  - anything else that names the old folder: python scripts/git/rename_check.py {old}")
    if not apply:
        print("\ndry run (re-run with --apply)")
    return 1 if failed else 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old", metavar="OLD_FOLDER", help="folder of the main checkout under --github")
    ap.add_argument("new", metavar="NEW_NAME", help="new folder and repo name")
    ap.add_argument("--github", type=Path, default=gitrepos.GITHUB, help=f"folder of repo checkouts (default {gitrepos.GITHUB})")
    ap.add_argument("--claude-projects", type=Path, default=Path.home() / ".claude" / "projects",
                    help="Claude Code's project folders (default ~/.claude/projects)")
    ap.add_argument("--apply", action="store_true", help="act (default is a dry run)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    return rename(a.github.absolute(), a.claude_projects, a.old, a.new, a.apply)


if __name__ == "__main__":
    sys.exit(main())
