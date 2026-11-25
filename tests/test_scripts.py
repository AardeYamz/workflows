"""Smoke tests for scripts/: Node syntax, relocate.mjs argument checks, and the worktrees.py, repos.py and projects.py command lines."""
import glob
import os
import shutil
import subprocess
import sys
import unittest

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
