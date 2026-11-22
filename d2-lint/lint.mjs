// Lints every tracked *.d2 file, and every ```d2 block in tracked Markdown, by compiling it with D2.js
// (the official WASM build of d2, so no binary is needed). Compiling runs the parser, the compiler and the layout,
// so it catches syntax errors, bad keywords and unknown references, not just malformed text.
// Usage: run it from inside the repo whose diagrams you want to check: node <path>/d2-lint/lint.mjs [file.d2|file.md ...]
// Exits 1 if anything fails. Under GitHub Actions it also writes ::error annotations.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { D2 } from '@terrastruct/d2';

const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
const args = process.argv.slice(2);
const files = args.length
  ? args
  : execFileSync('git', ['ls-files', '-z', '--', '*.d2', '*.md'], { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean);

const toRel = (file) => path.relative(root, path.resolve(root, file)).split(path.sep).join('/');

// Every tracked .d2 file, keyed by repo-relative path, so `@import`s between them resolve.
const d2fs = {};
for (const f of execFileSync('git', ['ls-files', '-z', '--', '*.d2'], { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean)) {
  d2fs[f] = readFileSync(path.join(root, f), 'utf8');
}

// Same fence rules as mermaid-lint: ``` or ~~~ then "d2", closed by the same character at least as long.
function blocks(text) {
  const out = [];
  let open = null;
  text.split(/\r?\n/).forEach((line, i) => {
    if (!open) {
      const m = /^\s*(`{3,}|~{3,})\s*d2\s*$/.exec(line);
      if (m) open = { fence: m[1], start: i + 2, lines: [] };
    } else if (new RegExp(`^\\s*${open.fence[0]}{${open.fence.length},}\\s*$`).test(line)) {
      out.push({ start: open.start, code: open.lines.join('\n') });
      open = null;
    } else {
      open.lines.push(line);
    }
  });
  if (open) out.push({ start: open.start, code: open.lines.join('\n'), unclosed: true });
  return out;
}

// Each unit is one diagram: a whole .d2 file (start = line 1) or a Markdown block (start = its first line).
const units = [];
for (const file of files) {
  const rel = toRel(file);
  const text = readFileSync(path.resolve(root, file), 'utf8');
  if (rel.endsWith('.d2')) units.push({ rel, start: 1, code: text, inputPath: rel });
  else for (const b of blocks(text)) units.push({ rel, ...b });
}

// D2.js rejects with a JSON list of {range, errmsg}, each errmsg reading "<path>:<line>:<col>: <message>".
// Fall back to the raw text for anything else (a WASM or layout failure).
function errors(e) {
  const raw = String(e?.message ?? e);
  try {
    const list = JSON.parse(raw);
    if (Array.isArray(list)) {
      return list.map((x) => {
        const m = /:(\d+):\d+: (.*)$/s.exec(x.errmsg ?? '');
        return m ? { line: Number(m[1]), message: m[2] } : { line: 1, message: x.errmsg ?? JSON.stringify(x) };
      });
    }
  } catch { /* not JSON */ }
  return [{ line: 1, message: raw.split('\n').slice(0, 5).join('\n') }];
}

const d2 = new D2();
let failed = 0;
for (const u of units) {
  let found = [];
  if (u.unclosed) found = [{ line: 1, message: 'd2 block is never closed' }];
  else {
    const inputPath = u.inputPath ?? `${u.rel}.block.d2`;
    try {
      await d2.compile({ fs: { ...d2fs, [inputPath]: u.code }, inputPath });
    } catch (e) {
      found = errors(e);
    }
  }
  if (!found.length) continue;
  failed++;
  for (const { line: at, message } of found) {
    const line = u.start + at - 1;
    console.error(`${u.rel}:${line}: ${message}`);
    if (process.env.GITHUB_ACTIONS) console.error(`::error file=${u.rel},line=${line}::${message.split('\n')[0]}`);
  }
}
console.log(`${units.length} d2 diagram${units.length === 1 ? '' : 's'} checked, ${failed} failed`);
// D2.js keeps a worker thread alive; exit explicitly.
process.exit(failed ? 1 : 0);
