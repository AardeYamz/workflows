---
name: github-desktop-relocate-repos
description: Move local git repos to a new folder and update GitHub Desktop (Windows) so it still finds them, instead of clicking Locate on each repo. Use when the user wants to reorganize, move, or rename folders that contain GitHub Desktop repositories, or change its default clone folder.
---

# Relocate repos in GitHub Desktop

GitHub Desktop stores each repo's path in an IndexedDB (`%APPDATA%\GitHub Desktop\IndexedDB`) and the default clone folder in localStorage (`last-clone-location`). After moving folders, the repos show "Can't find". `relocate.mjs github-desktop` (next to this file) fixes the stored paths in one pass.

## Steps

1. **Move the folders on disk first** (PowerShell; same-drive moves are instant): `Move-Item -LiteralPath <src> -Destination <dst>` per item. Check the destination doesn't already exist and report failures.
2. **Dry run** (shows what would change, writes nothing):
   `node "<this dir>\relocate.mjs" github-desktop --from "C:\old\parent" --to "C:\new\parent"`
3. **Apply** by adding `--apply`, plus `--clone-dir "C:\new\parent"` if the default clone folder should change too.
4. Check the output lists the expected repos and no `WARNING: new path missing on disk`, then open one repo in GitHub Desktop.

`--from`/`--to` are folder prefixes, matched case-insensitively. A single repo works too (`--from C:\a\repo --to C:\b\repo`). Run once per prefix pair. Repos that don't match `--from` are left untouched.

## Add existing clones

`node "<this dir>\relocate.mjs" github-desktop-add --path "C:\GitHub\repo" [--path ...] [--apply]` adds local clones to GitHub Desktop's list (dry run by default; same close/backup/relaunch as above; repos already listed are skipped).

## Notes

- The script strips `ELECTRON_RUN_AS_NODE` (set by VS Code / Claude Code) when launching GitHub Desktop; with it set the app starts as plain Node and rejects the debug flags.
- A dry run touches nothing (it only prints what it would do). With `--apply` the script closes GitHub Desktop, backs up IndexedDB to `%TEMP%\ghd-indexeddb-backup-<timestamp>`, launches the app with `--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1`, rewrites paths over the DevTools protocol, then relaunches normally. To restore: close the app and copy the backup over `IndexedDB`.
- Launching with a debug port may be refused by auto-mode permission classifiers. Explain what it does and get explicit user approval first.
- `relocate.mjs claude-sessions --from ... --to ...` does the same for Claude Code session folders in `~/.claude/projects` (files that already exist at the target stay in the old folder, which is then kept; backup in `%TEMP%`).
- Requires Node 22+ (built-in `fetch` and `WebSocket`). Windows only.
- To use as a Claude Code skill, copy or symlink this folder into `~/.claude/skills/`.
