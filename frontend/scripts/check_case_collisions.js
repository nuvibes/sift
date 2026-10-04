#!/usr/bin/env node
/*
 * Two files whose paths differ only by capitals, or a module a component's name can shadow.
 *
 * Linux tells those files apart. Windows and macOS do not, so an import resolves to whichever one
 * the filesystem hands back, and it is not the one the author meant. `Tunnels.svelte` beside a
 * state module `tunnels.svelte.ts`, imported as `./tunnels.svelte`, resolves to the state module on
 * Linux and to the component on Windows, where the production build fails with "Tunnels is not
 * exported by tunnels.svelte". Sift ships on Windows, and a Mac's filesystem is case-insensitive by
 * default too.
 *
 * The rule is therefore about importability, not tidiness: within one folder, no module may have a
 * name that a sibling component's name collapses onto. State modules are named for what they hold
 * with a `-state` suffix where the component already owns the plain word.
 *
 * Run: node scripts/check_case_collisions.js
 */
import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

// fileURLToPath, not `.pathname`. On Windows a file URL's pathname is "/C:/work/..." with a leading
// slash, which is not a path any filesystem call accepts: it silently becomes "C:\C:\work\...".
const SRC = fileURLToPath(new URL('../src', import.meta.url));

function walk(dir) {
	const out = [];
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) out.push(...walk(full));
		else out.push(full);
	}
	return out;
}

const files = walk(SRC);
const problems = [];

/* 1. Two paths that a case-insensitive filesystem cannot keep apart at all. One of them simply
 *    will not exist after a checkout on Windows or macOS. */
const byLowerPath = new Map();
for (const f of files) {
	const key = f.toLowerCase();
	const seen = byLowerPath.get(key);
	if (seen !== undefined) {
		problems.push(
			`two paths differ only by capitals, so only one survives a checkout on Windows or macOS:\n` +
				`      ${relative(SRC, seen)}\n      ${relative(SRC, f)}`
		);
	}
	byLowerPath.set(key, f);
}

/* 2. A module whose import specifier a sibling's filename can capture. `x.svelte.ts` is imported as
 *    `./x.svelte`, and on a case-insensitive filesystem `X.svelte` answers to that. */
const components = new Set();
for (const f of files) {
	if (f.endsWith('.svelte') && !f.endsWith('.svelte.ts')) components.add(f.toLowerCase());
}
for (const f of files) {
	if (!f.endsWith('.svelte.ts') || f.endsWith('.svelte.test.ts')) continue;
	const asImported = f.slice(0, -3).toLowerCase(); // drop ".ts" -> what the import writes
	if (components.has(asImported)) {
		problems.push(
			`this module is imported as "${relative(SRC, f).slice(0, -3)}", which a case-insensitive\n` +
				`      filesystem resolves to the component beside it instead:\n` +
				`      module:    ${relative(SRC, f)}\n` +
				`      shadowed by: ${relative(SRC, f.slice(0, -3))}\n` +
				`      Rename the module: the convention is a "-state" suffix.`
		);
	}
}

if (problems.length > 0) {
	console.error(`check_case_collisions: ${problems.length} problem(s)\n`);
	for (const p of problems) console.error(`  - ${p}\n`);
	process.exit(1);
}
console.log(
	`check_case_collisions: ${files.length} files, no name a case-insensitive disk confuses`
);
