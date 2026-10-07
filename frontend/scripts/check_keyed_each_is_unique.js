// A keyed `{#each}` whose key falls back to a constant must carry the loop position too.
//
// ## The fault
//
// Svelte's keyed each promises that the key names ONE row. Break that and it is not a row drawn
// wrongly. It is `Cannot have duplicate keys in a keyed each`, thrown while rendering, which
// takes the screen out.
//
// The way it gets broken is always the same and always looks careful: a key reaches for an
// identifier that is sometimes absent and names a fallback for when it is.
//
//     {#each people as person (person.person_id ?? 'nameless')}
//     {#each sources as source (source.source_type + (source.source_id ?? ''))}
//
// A fallback is a CONSTANT. Every entry that needs it gets the same one, so the moment two entries
// are missing that field at the same time there are two rows under one key. The author knew the
// field was nullable (that is what the `??` says) and then keyed on it anyway.
//
// ## The rule
//
// A key expression containing `??` must also read the loop's index, which is the one thing that
// cannot repeat:
//
//     {#each people as person, at (`${at}:${person.person_id ?? 'nameless'}`)}
//
// ## What this deliberately does NOT try to decide
//
// Whether any OTHER key is unique. `(box)` over a list of stash-box names is a duplicate waiting
// for two boxes with one name; `(STARS)` over a module constant cannot be. Nothing static can tell
// those apart, and a gate that guessed would be answered by being switched off. The `??` is the
// one case where the source itself says the field can be missing, so it is the one case a checker
// can be right about every time.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

/** `{#each <collection> as <binding>[, <index>] (<key>)}`, key taken to the closing brace. */
const KEYED = /\{#each\s+([\s\S]*?)\s+as\s+([^(]*?)\s*\(([\s\S]*?)\)\}/g;

async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) yield* walk(full);
		else if (entry.name.endsWith('.svelte')) yield full;
	}
}

const offenders = [];
let keyed = 0;

for await (const full of walk(SOURCE)) {
	const where = relative(SOURCE, full).split('\\').join('/');
	const text = await readFile(full, 'utf8');
	for (const [, , binding, key] of text.matchAll(KEYED)) {
		keyed += 1;
		if (!key.includes('??')) continue;
		/* The index is the second name in the binding: `as person, at`. Named AND read: a
		   declared index that the key never mentions is not in the key. */
		const index = binding.includes(',') ? binding.split(',')[1].trim() : null;
		if (index && new RegExp(`\\b${index}\\b`).test(key)) continue;
		offenders.push(`${where}  (${key.trim()})`);
	}
}

if (offenders.length > 0) {
	console.error(
		`\n${offenders.length} keyed each(es) key on a value that falls back to a constant.\n\n` +
			`  Every entry missing that field gets the SAME key, which is a duplicate key and a\n` +
			`  thrown error rather than a row drawn wrongly. Add the loop position:\n\n` +
			`      {#each people as person, at (\`\${at}:\${person.person_id ?? 'nameless'}\`)}\n`
	);
	for (const line of offenders) console.error(`    ${line}`);
	process.exit(1);
}

console.log(`Keyed eaches: ${keyed} read, none keyed on a fallback constant.`);
