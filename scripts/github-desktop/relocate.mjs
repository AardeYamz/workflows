#!/usr/bin/env node
// Fixes what points at a folder after it was moved on disk. Windows only, Node 22+.
//
//   node relocate.mjs github-desktop --from "C:\old\dir" --to "C:\new\dir" [--clone-dir "C:\new\dir"] [--apply]
//       Rewrites repo paths in GitHub Desktop's IndexedDB (and optionally its default clone folder).
//   node relocate.mjs github-desktop-add --path "C:\a\repo" [--path ...] [--apply]
//       Adds existing local clones to GitHub Desktop's repo list.
//   node relocate.mjs claude-sessions --from "C:\old\dir" --to "C:\new\dir" [--apply]
//       Re-homes Claude Code session folders in ~/.claude/projects so VS Code / the CLI list them
//       under the new folder. Only folders and files are moved; transcripts are never edited.
//
// Both are dry runs unless --apply. A dry run touches nothing: it does not close or launch GitHub
// Desktop (listing its repos needs the app's database, so a dry run only says what it would do).
import { spawn, execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// ---- shared helpers -------------------------------------------------------------------------
const args = process.argv.slice(2);
const command = args[0];
const opt = (n) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : undefined; };
const apply = args.includes('--apply');
const BS = String.fromCharCode(92);
const norm = (p) => p.split('/').join(BS).replace(/[\\]+$/, '');
const stamp = () => new Date().toISOString().replace(/[:.]/g, '-');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const tmp = process.env.TEMP || os.tmpdir();
// VS Code / Claude Code set ELECTRON_RUN_AS_NODE, which makes GitHub Desktop (Electron) start as plain Node.
const cleanEnv = { ...process.env }; delete cleanEnv.ELECTRON_RUN_AS_NODE;

const COMMANDS = { 'github-desktop': githubDesktop, 'github-desktop-add': githubDesktopAdd, 'claude-sessions': claudeSessions };
if (!COMMANDS[command]) {
  console.error('usage: relocate.mjs github-desktop|claude-sessions --from DIR --to DIR [--clone-dir DIR] [--apply]\n       relocate.mjs github-desktop-add --path DIR [--path DIR ...] [--apply]');
  process.exit(2);
}
if (command !== 'github-desktop-add' && (!opt('--from') || !opt('--to'))) { console.error('need --from and --to'); process.exit(2); }
const FROM = opt('--from') && norm(opt('--from')), TO = opt('--to') && norm(opt('--to'));
await COMMANDS[command]();
process.exit(0);

// ---- github-desktop -------------------------------------------------------------------------
// Closes GitHub Desktop, backs up its IndexedDB, relaunches it with a localhost debug port, runs
// `fn(...fnArgs)` inside the renderer, then relaunches it normally. Returns { result, backup }.
async function inDesktop(fn, fnArgs) {
  const PORT = 9222;
  const local = process.env.LOCALAPPDATA, roaming = process.env.APPDATA;
  const kill = () => { try { execFileSync('taskkill', ['/F', '/IM', 'GitHubDesktop.exe'], { stdio: 'ignore' }); } catch {} };
  // 1. Close GitHub Desktop (it holds the DB open) and back up its IndexedDB.
  kill();
  await sleep(2500);
  const backup = path.join(tmp, `ghd-indexeddb-backup-${stamp()}`);
  cpSync(path.join(roaming, 'GitHub Desktop', 'IndexedDB'), backup, { recursive: true });
  console.log('backup:', backup);

  // 2. Launch the newest app-* build with a localhost-only debug port.
  const appDir = readdirSync(path.join(local, 'GitHubDesktop')).filter(d => /^app-/.test(d) && existsSync(path.join(local, 'GitHubDesktop', d, 'GitHubDesktop.exe')))
    .sort((x, y) => x.localeCompare(y, undefined, { numeric: true })).pop();
  const exe = path.join(local, 'GitHubDesktop', appDir, 'GitHubDesktop.exe');
  spawn(exe, [`--remote-debugging-port=${PORT}`, '--remote-debugging-address=127.0.0.1'], { detached: true, stdio: 'ignore', env: cleanEnv }).unref();

  let page;
  for (let i = 0; i < 30 && !page; i++) {
    await sleep(1000);
    try { page = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(p => p.type === 'page'); } catch {}
  }
  if (!page) { console.error('debug port never came up'); process.exit(1); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);

  const expr = `(${fn})(${fnArgs.map(a => JSON.stringify(a)).join(',')})`;
  const result = await new Promise((res, rej) => {
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id !== 1) return;
      m.result.exceptionDetails ? rej(new Error(JSON.stringify(m.result.exceptionDetails))) : res(JSON.parse(m.result.result.value));
    };
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
  });
  ws.close();
  kill();
  await sleep(2500);
  spawn(path.join(local, 'GitHubDesktop', 'GitHubDesktop.exe'), [], { detached: true, stdio: 'ignore', env: cleanEnv }).unref();
  console.log('GitHub Desktop relaunched. Restore from backup if needed:', backup);
  return result;
}

async function githubDesktop() {
  const cloneDir = opt('--clone-dir');

  if (!apply) {
    console.log(`DRY RUN: would close GitHub Desktop, back up its IndexedDB to ${tmp}, rewrite every repo path under`);
    console.log(`  ${FROM}\n  -> ${TO}`);
    if (cloneDir) console.log(`and set the default clone folder to ${norm(cloneDir)}, then relaunch it.`);
    if (!existsSync(TO)) console.log(`WARNING: ${TO} does not exist on disk yet (move the folders first).`);
    return;
  }

  // 3. Runs inside the renderer: rewrite matching repository paths (and optionally the clone dir).
  const inPage = async (FROM, TO, cloneDir) => {
    const BS = String.fromCharCode(92);
    const db = await new Promise(res => { const q = indexedDB.open('Database'); q.onsuccess = () => res(q.result); });
    const tx = db.transaction('repositories', 'readwrite');
    const st = tx.objectStore('repositories');
    const all = await new Promise(r => { const q = st.getAll(); q.onsuccess = () => r(q.result); });
    const lc = FROM.toLowerCase(), changes = [];
    for (const r of all) {
      const p = r.path.split('/').join(BS);
      if (p.toLowerCase() === lc || p.toLowerCase().startsWith(lc + BS)) {
        const np = TO + p.slice(FROM.length);
        changes.push(r.path + ' -> ' + np);
        r.path = np;
        st.put(r);
      }
    }
    await new Promise(r => tx.oncomplete = r);
    if (cloneDir) localStorage.setItem('last-clone-location', cloneDir);
    return JSON.stringify({ total: all.length, changes });
  };
  const result = await inDesktop(inPage, [FROM, TO, cloneDir ? norm(cloneDir) : null]);
  console.log(`APPLIED: ${result.changes.length} of ${result.total} repos match`);
  result.changes.forEach(c => console.log('  ' + c));
  const missing = result.changes.map(c => c.split(' -> ')[1]).filter(p => !existsSync(p));
  if (missing.length) console.log('WARNING: new path missing on disk:\n  ' + missing.join('\n  '));
}

// ---- github-desktop-add ---------------------------------------------------------------------
// Adds existing local clones to GitHub Desktop's repository list (like File > Add local repository).
async function githubDesktopAdd() {
  const paths = args.flatMap((a, i) => a === '--path' ? [norm(args[i + 1])] : []);
  const missing = paths.filter(p => !existsSync(path.join(p, '.git')));
  if (missing.length) { console.error('not a git repo:\n  ' + missing.join('\n  ')); process.exit(1); }
  if (!apply) {
    console.log('DRY RUN: would close GitHub Desktop, back up its IndexedDB, add these repos (skipping ones already listed), relaunch:');
    paths.forEach(p => console.log('  ' + p));
    return;
  }
  const inPage = async (paths) => {
    const BS = String.fromCharCode(92);
    const db = await new Promise(res => { const q = indexedDB.open('Database'); q.onsuccess = () => res(q.result); });
    const tx = db.transaction('repositories', 'readwrite');
    const st = tx.objectStore('repositories');
    const all = await new Promise(r => { const q = st.getAll(); q.onsuccess = () => r(q.result); });
    const have = new Set(all.map(r => r.path.split('/').join(BS).toLowerCase()));
    const template = all.find(r => !r.isTutorialRepository) || {};
    const added = [], skipped = [];
    for (const p of paths) {
      if (have.has(p.toLowerCase())) { skipped.push(p); continue; }
      // Same shape as an existing record; GitHub Desktop links the GitHub remote itself on first fetch.
      const rec = { ...template, path: p, gitHubRepositoryID: null, missing: false, customAlias: null };
      delete rec.id;
      st.add(rec);
      added.push(p);
    }
    await new Promise(r => tx.oncomplete = r);
    return JSON.stringify({ total: all.length, added, skipped });
  };
  const r = await inDesktop(inPage, [paths]);
  console.log(`ADDED ${r.added.length}, already listed ${r.skipped.length} (had ${r.total} repos)`);
  r.added.forEach(p => console.log('  + ' + p));
  r.skipped.forEach(p => console.log('  = ' + p));
}

// ---- claude-sessions ------------------------------------------------------------------------
async function claudeSessions() {
  const enc = (p) => p.replace(/[^A-Za-z0-9]/g, '-');
  const root = path.join(os.homedir(), '.claude', 'projects');
  const fromEnc = enc(FROM).toLowerCase(), toEnc = enc(TO);
  const backupRoot = path.join(tmp, `claude-sessions-backup-${stamp()}`);
  let nDirs = 0, nFiles = 0, nSkipped = 0;

  for (const d of readdirSync(root, { withFileTypes: true }).filter(e => e.isDirectory())) {
    const ld = d.name.toLowerCase();
    if (ld !== fromEnc && !ld.startsWith(fromEnc + '-')) continue;
    // keep the source folder's drive-letter casing (VS Code uses "c--", the desktop app "C--")
    const target = d.name[0] + toEnc.slice(1) + d.name.slice(fromEnc.length);
    const src = path.join(root, d.name), dst = path.join(root, target);
    console.log(`${d.name}\n  -> ${target}${existsSync(dst) ? '  (merging into existing folder)' : ''}`);
    nDirs++;
    if (!apply) continue;
    mkdirSync(backupRoot, { recursive: true });
    cpSync(src, path.join(backupRoot, d.name), { recursive: true });
    mkdirSync(dst, { recursive: true });
    let skipped = 0;
    const walk = (dir, rel = '') => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const s = path.join(dir, e.name), r = path.join(rel, e.name), t = path.join(dst, r);
        if (e.isDirectory()) { mkdirSync(t, { recursive: true }); walk(s, r); continue; }
        if (existsSync(t)) { console.log('  SKIP (exists, left in the old folder):', r); skipped++; continue; }
        renameSync(s, t);
        nFiles++;
      }
    };
    walk(src);
    nSkipped += skipped;
    // Only remove the old folder when everything left it; skipped files stay put (and are backed up).
    if (!skipped) {
      try { rmSync(src, { recursive: true }); } catch (e) { console.log('  could not remove old folder:', e.message); }
    }
  }
  console.log(`${apply ? 'APPLIED' : 'DRY RUN'}: ${nDirs} session folder(s)` +
    (apply ? `, ${nFiles} file(s) moved${nSkipped ? `, ${nSkipped} skipped (old folder kept)` : ''}. Backup: ${backupRoot}` : ''));
}
