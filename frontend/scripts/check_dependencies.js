// Every runtime dependency of the client is on a list, with the reason it is there.
//
// ## Why
//
// The licence gate says a package may be shipped; nothing said a package may be ADDED. A second
// component library (another headless kit, a floating-position helper, a toast library) is the
// one dependency that would undo the design system, and it would arrive as one line in
// package.json and one import, both of which every other gate is blind to.
//
// So the runtime dependencies are an allowlist, and an entry carries the reason it is allowed. A
// package not on the list fails the build; a package on the list that package.json no longer names
// fails too, so the list cannot rot into a record of stale facts. Dev dependencies are not
// held to this: a linter or a test runner draws nothing.
//
// The list lives in `scripts/dependencies.json` beside this script rather than in this file, for
// the reason every other list here does: a reason is written while looking at the package, not
// while looking at the gate.

import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PACKAGE = join(HERE, '..', 'package.json');
const ALLOWED = join(HERE, 'dependencies.json');

const declared = Object.keys(JSON.parse(await readFile(PACKAGE, 'utf8')).dependencies ?? {});
const allowed = JSON.parse(await readFile(ALLOWED, 'utf8'));

const complaints = [];

for (const name of declared) {
	const reason = allowed[name];
	if (typeof reason !== 'string' || reason.trim().length < 20) {
		complaints.push(
			`${name} is a runtime dependency and scripts/dependencies.json does not say why.\n` +
				`    Add it with one sentence: what it does that nothing already here does. A second\n` +
				`    component library, a floating-position helper or a toast kit is not a reason.`
		);
	}
}

for (const name of Object.keys(allowed)) {
	if (name.startsWith('_')) continue;
	if (!declared.includes(name)) {
		complaints.push(
			`${name} is in scripts/dependencies.json and package.json no longer names it. Take it out.`
		);
	}
}

/* A list that allows nothing is a gate reading the wrong file. */
if (declared.length < 5) {
	console.error(
		`\ndependencies: package.json names only ${declared.length} runtime dependencies. That is too few to be right.\n`
	);
	process.exit(1);
}

if (complaints.length > 0) {
	console.error('\nEvery runtime dependency says why it is here.\n');
	for (const complaint of complaints) console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(`dependencies: ${declared.length} runtime dependencies, every one with a reason`);
