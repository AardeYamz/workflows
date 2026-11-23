"""Smoke tests for scripts/: Node syntax, relocate.mjs argument checks and the worktrees.py command line."""
import glob
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "scripts", "git"))


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


if __name__ == "__main__":
    unittest.main()
