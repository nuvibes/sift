// Every comment in the client and the desktop shell, read against the words in
// `tests/gates/data/narration.json`: dates, who asked and in which working session, the history of
// a rule, pointers into documents this repository does not hold, paths on one development machine.
// Held at zero. The Python half is `tests/gates/test_no_narration_in_comments.py`.
//
// A comment says why the code is as it is now. A number the code depends on may stay; when, where
// and by whom it was found goes, and the story belongs in the commit message.

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { narrationEntries, narrationIn } from './lib/narration.js';
import { FRONTEND } from './lib/tree.js';

const REPO = join(FRONTEND, '..');

/** What is read: everything tracked under the client and the shell that carries comments in this
 * syntax, the native addon's C++ included. */
const READ = /\.(ts|js|mjs|cjs|mts|svelte|css|cc|h)$/;

/** Generated from the server's docstrings, which the Python half reads at the source. */
const GENERATED = ['frontend/src/lib/api/schema.d.ts', '/generated/'];

/** What a gate test plants into the tree for a moment; see `tests/gates/__init__.py`. */
const PLANTED = 'GateFixture';

const entries = narrationEntries();

/* The known positives, every run: each entry against its example and its near miss, and the
   reader against a comment in each place one can sit. A reader that finds nothing reads exactly
   like a tree with nothing to say. */
const broken = entries.flatMap((entry) => {
	entry.pattern.lastIndex = 0;
	const hits = entry.pattern.test(entry.example);
	entry.pattern.lastIndex = 0;
	const misses = !entry.pattern.test(entry.clean);
	entry.pattern.lastIndex = 0;
	return hits && misses ? [] : [`${entry.family}: ${entry.source}`];
});
const stamp = '20' + '26-01-02';
const planted = [
	`<script lang="ts">\n\t// a line, ${stamp}\n</script>\n`,
	`<script lang="ts"></script>\n<!-- markup, ${stamp} -->\n<p>x</p>\n`,
	`/**\n * a block,\n * ${stamp}\n */\nconst x = 1;\n`
];
const seen = planted.map((text) => narrationIn(text, entries).map((one) => one.line));
if (JSON.stringify(seen) !== JSON.stringify([[2], [2], [3]])) {
	broken.push(`the comment reader found ${JSON.stringify(seen)} where [[2],[2],[3]] was planted`);
}
if (broken.length > 0) {
	console.error('\nnarration: the list or its reader cannot find what it is for, so it would pass');
	console.error('any tree:\n' + broken.map((one) => `  ${one}`).join('\n'));
	process.exit(1);
}

const listed = spawnSync('git', ['ls-files', '-z', '--', 'frontend', 'desktop'], {
	cwd: REPO,
	encoding: 'utf8'
});
if (listed.status !== 0 || listed.error) {
	console.error(`narration: git ls-files failed: ${listed.error ?? listed.stderr}`);
	process.exit(1);
}
const files = listed.stdout
	.split('\0')
	.filter((path) => READ.test(path))
	.filter((path) => !GENERATED.some((part) => path.includes(part)))
	.filter((path) => !path.split('/').pop()?.startsWith(PLANTED));

if (files.length < 500) {
	console.error(`narration: only ${files.length} files listed under frontend/ and desktop/.`);
	process.exit(1);
}

/** @type {string[]} */
const offenders = [];
/** @type {Map<string, number>} */
const byFamily = new Map();
for (const path of files) {
	let text;
	try {
		text = readFileSync(join(REPO, path), 'utf8');
	} catch {
		continue; // deleted in the working tree, still in the index
	}
	for (const one of narrationIn(text, entries)) {
		offenders.push(`  ${path}:${one.line}  [${one.family}] ${JSON.stringify(one.found)}`);
		byFamily.set(one.family, (byFamily.get(one.family) ?? 0) + 1);
	}
}

if (offenders.length > 0) {
	console.error(`\nnarration: ${offenders.length} lines of comment narrate rather than explain.`);
	for (const [family, count] of byFamily) console.error(`  ${family}: ${count}`);
	console.error('');
	console.error(offenders.slice(0, 60).join('\n'));
	if (offenders.length > 60) console.error(`  ... and ${offenders.length - 60} more`);
	console.error(
		'\n  Say why the code is as it is now, in a short paragraph at most. Keep a number the code depends on;\n' +
			'  drop when, where and by whom it was found. The story belongs in the commit message.\n'
	);
	process.exit(1);
}

console.log(`narration: ${files.length} files read; no comment narrates`);
