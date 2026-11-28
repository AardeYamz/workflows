"""Tests for scripts/git/rename_clone.py and rename_check.py, on throwaway repos."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "scripts", "git"))
sys.path.insert(0, os.path.join(REPO, "scripts"))


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


if __name__ == "__main__":
    unittest.main()
