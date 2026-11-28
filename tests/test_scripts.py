"""Smoke tests for scripts/: Node syntax, relocate.mjs argument checks, and the worktrees.py, repos.py and projects.py command lines, and the rename scripts."""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "scripts", "git"))
sys.path.insert(0, os.path.join(REPO, "scripts", "github"))
sys.path.insert(0, os.path.join(REPO, "scripts"))


class SyntaxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node not installed")
    def test_node_scripts_parse(self):
        files = glob.glob(os.path.join(REPO, "scripts", "**", "*.mjs"), recursive=True)
        self.assertTrue(files)
        for f in files:
            r = subprocess.run(["node", "--check", f], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + "\n" + r.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node not installed")
    def test_relocate_needs_a_command_and_paths(self):
        script = os.path.join(REPO, "scripts", "github-desktop", "relocate.mjs")
        self.assertEqual(subprocess.run(["node", script], capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run(["node", script, "claude-sessions"], capture_output=True).returncode, 2)


class RenameScriptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.github, self.projects = self.tmp / "GitHub", self.tmp / "projects"
        self.repo = self.github / "old-repo"
        self.repo.mkdir(parents=True)
        for args in (["init", "-q", "-b", "main"], ["remote", "add", "origin", "https://github.com/AardeYamz/old-repo.git"]):
            self.git(self.repo, *args)

    def git(self, cwd, *args):
        who = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        return subprocess.run(["git", "-C", str(cwd), *who, *args], capture_output=True, text=True, check=True).stdout

    def commit(self, files):
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text, encoding="utf-8")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "x")

    def run_script(self, name, *args):
        script = os.path.join(REPO, "scripts", "git", name)
        return subprocess.run([sys.executable, script, *args, "--github", str(self.github)], capture_output=True, text=True)

    def test_rename_clone(self):
        import rename_clone
        self.commit({})
        outside, inside = self.github / ".worktrees" / "old-repo-x", self.repo / ".worktrees" / "y"
        self.git(self.repo, "worktree", "add", "-q", str(outside), "-b", "x")
        self.git(self.repo, "worktree", "add", "-q", str(inside), "-b", "y")
        (self.github / "old-repo-acl").mkdir()  # a sibling whose name starts the same
        enc = rename_clone.encode(self.repo)
        for suffix in ("", "--worktrees-y", "-acl", "-src"):
            (self.projects / (enc + suffix) / "memory").mkdir(parents=True)
            (self.projects / (enc + suffix) / "memory" / "MEMORY.md").write_text(suffix, encoding="utf-8")
        ws = self.github / "a.code-workspace"
        ws.write_text('{"folders": [{"path": "old-repo"}, {"path": "./old-repo/src"}, {"path": "old-repo-acl"},\n'
                      '  {"path": "%s", "name": "old-repo"}]}' % self.repo.as_posix(), encoding="utf-8")
        args = ("old-repo", "new-repo", "--claude-projects", str(self.projects))

        dry = self.run_script("rename_clone.py", *args)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertTrue(self.repo.is_dir() and (self.projects / enc).is_dir())

        done = self.run_script("rename_clone.py", *args, "--apply")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        new = self.github / "new-repo"
        self.assertFalse(self.repo.exists())
        self.assertEqual(self.git(new, "remote", "get-url", "origin").strip(), "https://github.com/AardeYamz/new-repo.git")
        for tree in (outside, new / ".worktrees" / "y"):
            self.git(tree, "status")  # raises if the worktree lost its checkout
        self.assertIn(str(new / ".worktrees" / "y"), self.git(new, "worktree", "list"))
        new_enc = rename_clone.encode(new)
        self.assertEqual((self.projects / new_enc / "memory" / "MEMORY.md").read_text(encoding="utf-8"), "")
        self.assertTrue((self.projects / (new_enc + "--worktrees-y")).is_dir())
        self.assertTrue((self.projects / (enc + "-acl")).is_dir())  # the sibling's memory stays
        self.assertTrue((self.projects / (enc + "-src")).is_dir())  # ambiguous: listed, not moved
        self.assertIn(enc + "-src", done.stdout)
        text = ws.read_text(encoding="utf-8")
        for expected in ('"new-repo"}', '"./new-repo/src"', '"old-repo-acl"',
                         '"%s", "name": "old-repo"' % new.as_posix()):
            self.assertIn(expected, text)

    def test_new_url_keeps_the_form(self):
        import rename_clone
        for url, expected in (("https://github.com/o/a", "https://github.com/o/b"),
                              ("git@github.com:o/a.git", "git@github.com:o/b.git")):
            self.assertEqual(rename_clone.new_url(url, "b"), expected)

    def test_rename_clone_refuses_an_existing_target(self):
        (self.github / "new-repo").mkdir()
        r = self.run_script("rename_clone.py", "old-repo", "new-repo", "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.repo.is_dir())

    def test_rename_check(self):
        self.commit({"ci.yml": "uses: AardeYamz/old-dep/x@1\nrun: cat ../old-dep/y\nz: AardeYamz/old-dep-acl\n",
                     "docs/a.txt": "~/GitHub/old-dep\n", "vendor/old-dep/f.js": "ok\n"})
        args = ("rename_check.py", "old-dep", "--no-fetch", "--claude-md", str(self.tmp / "none"))
        r = self.run_script(*args)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("3 hit(s) in code, 1 in docs", r.stdout)
        self.assertIn("vendor/old-dep/f.js  (path)", r.stdout)
        self.assertNotIn("old-dep-acl", r.stdout)
        self.assertEqual(self.run_script("rename_check.py", "unused", "--no-fetch", "--claude-md",
                                         str(self.tmp / "none")).returncode, 0)


class WorktreesCliTest(unittest.TestCase):
    def test_subcommands(self):
        import worktrees
        p = worktrees.build_parser()
        self.assertTrue(p.parse_args(["sync", "--apply"]).apply)
        a = p.parse_args(["prune", "--remote", "--no-fetch"])
        self.assertTrue(a.remote and a.no_fetch and not a.apply)
        with self.assertRaises(SystemExit):
            p.parse_args(["sync", "--remote"])  # --remote belongs to prune only
        with self.assertRaises(SystemExit):
            p.parse_args([])


    def test_repo_selection(self):
        from common import gitrepos
        self.assertIsNone(gitrepos.selection(None))
        self.assertIsNone(gitrepos.selection(["all"]))
        self.assertIsNone(gitrepos.selection([" "]))
        self.assertEqual(gitrepos.selection("trips"), {"trips"})
        self.assertEqual(gitrepos.selection(["trips,yl-theme", "workflows lam-yan-lik"]),
                         {"trips", "yl-theme", "workflows", "lam-yan-lik"})


class ReposCliTest(unittest.TestCase):
    def test_subcommands(self):
        import repos
        p = repos.build_parser()
        a = p.parse_args(["builds", "--repo", "trips", "--repo", "yl-theme", "--workflow", "build.yml,ci.yml", "--prs"])
        self.assertEqual(a.repo, ["trips", "yl-theme"])
        self.assertEqual(repos.split_names(a.workflow), ["build.yml", "ci.yml"])
        self.assertTrue(a.prs and not a.apply)
        self.assertTrue(p.parse_args(["sync-prs", "--apply"]).apply)
        with self.assertRaises(SystemExit):
            p.parse_args(["sync-prs", "--prs"])  # --prs belongs to builds only

    def test_archive_selection(self):
        import datetime
        import re
        import repos

        def repo(name, head, other=None, issues=0):
            commit = lambda date: {"committedDate": date}
            refs = [{"name": "main", "target": commit(head)}] if head else []
            if other:
                refs.append({"name": "side", "target": commit(other)})
            return {"name": name, "createdAt": "2019-01-01T00:00:00Z", "issues": {"totalCount": issues},
                    "pullRequests": {"totalCount": 0}, "refs": {"nodes": refs},
                    "defaultBranchRef": {"target": commit(head)} if head else None}

        old = "2019-10-04T06:04:02Z"
        have = [repo("326_Lab1", old, other="2026-05-01T00:00:00-05:00"), repo("empty", None),
                repo("live", "2026-01-01T00:00:00-05:00"), repo("busy", old, issues=2)]
        today = datetime.date(2026, 6, 1)
        plan = {n: (newer, why) for n, _, newer, why in
                repos.archive_plan(have, today, older_than=730, exclude=["EMPTY"])}
        self.assertEqual(sorted(plan), ["326_Lab1", "busy", "empty"])
        self.assertEqual(plan["326_Lab1"], (["side"], None))  # a newer side branch is reported, not a veto
        self.assertEqual(plan["empty"], ([], "excluded"))
        self.assertIn("open", plan["busy"][1])
        self.assertEqual([p[0] for p in repos.archive_plan(have, today, match=re.compile("^[23][0-9]{2}_"))],
                         ["326_Lab1"])
        self.assertIsNone(repos.archive_plan(have, today, only=["busy"], include_open=True)[0][3])

    def test_archive_needs_a_filter(self):
        import repos
        a = repos.build_parser().parse_args(["archive"])
        self.assertFalse(a.apply)
        with self.assertRaises(SystemExit):
            repos.cmd_archive(a)
        with self.assertRaises(SystemExit):
            repos.build_parser().parse_args(["archive", "--older-than", "0"])

    def test_triggers(self):
        import repos
        self.assertEqual(repos.triggers("on: push\n"), {"push"})
        self.assertEqual(repos.triggers("on: [push, 'pull_request']\n"), {"push", "pull_request"})
        block = ("name: CI\non:\n  push:\n    branches: [main]\n  # note\n  pull_request:\n  workflow_dispatch:\n"
                 "    inputs:\n      x:\n        type: string\njobs:\n  a:\n")
        self.assertEqual(repos.triggers(block), {"push", "pull_request", "workflow_dispatch"})
        self.assertEqual(repos.triggers('"on":\n  - push\n  - workflow_dispatch\n'), {"push", "workflow_dispatch"})
        self.assertEqual(repos.triggers("on: # zizmor: ignore[x]\n  workflow_run:\n    types: [completed]\n"),
                         {"workflow_run"})
        self.assertEqual(repos.triggers("jobs: {}\n"), set())
        self.assertEqual(repos.workflow_name("name: 'Build & deploy'\non: push\n", "b.yml"), "Build & deploy")
        self.assertEqual(repos.workflow_name("on: push\n", "b.yml"), "b.yml")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class SyncPrTest(unittest.TestCase):
    """repos.sync_pr against a local bare origin: a clean merge, a CI-owned path, a conflict."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        origin, self.repo = os.path.join(self.tmp, "origin.git"), os.path.join(self.tmp, "repo")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", origin)
        self.git(self.tmp, "clone", "-q", origin, self.repo)
        self.commit({"a.txt": "a", "ver.txt": "1"}, "base")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"b.txt": "b", "ver.txt": "feat"}, "feat")
        self.git(self.repo, "push", "-q", "origin", "feat")
        self.git(self.repo, "checkout", "-q", "main")
        self.commit({"a.txt": "a2", "ver.txt": "2"}, "main moves on")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "fetch", "-q", "origin")

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        r = subprocess.run(["git", "-C", cwd, "-c", "commit.gpgsign=false", *args],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, files, msg):
        for name, text in files.items():
            with open(os.path.join(self.repo, name), "w") as f:
                f.write(text)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", msg)

    def sync(self, ci_owned, apply):
        import repos
        os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return repos.sync_pr(self.repo, "main", 1, "feat", ci_owned, apply)

    def test_conflict_is_left_alone(self):
        before = self.git(self.repo, "rev-parse", "origin/feat")
        self.assertEqual(self.sync([], True), "conflict")
        self.git(self.repo, "fetch", "-q", "origin")
        self.assertEqual(self.git(self.repo, "rev-parse", "origin/feat"), before)
        self.assertEqual(len(self.git(self.repo, "worktree", "list").splitlines()), 1)  # throwaway worktree removed

    def test_ci_owned_path_takes_main(self):
        self.assertEqual(self.sync(["ver.txt"], False), "updated")  # dry run: merges cleanly, pushes nothing
        self.assertEqual(self.sync(["ver.txt"], True), "updated")
        self.git(self.repo, "fetch", "-q", "origin")
        self.assertEqual(self.git(self.repo, "show", "origin/feat:ver.txt"), "2")
        self.assertEqual(self.git(self.repo, "show", "origin/feat:b.txt"), "b")
        self.assertEqual(self.sync(["ver.txt"], True), "current")


if __name__ == "__main__":
    unittest.main()


class ProjectsCliTest(unittest.TestCase):
    def test_archive_closed_is_a_dry_run_by_default(self):
        import projects
        p = projects.build_parser()
        self.assertFalse(p.parse_args(["archive-closed"]).apply)
        a = p.parse_args(["archive-closed", "--project", "2,5", "--owner", "x", "--apply"])
        self.assertTrue(a.apply and a.project == "2,5" and a.owner == "x")
        with self.assertRaises(SystemExit):
            p.parse_args([])
