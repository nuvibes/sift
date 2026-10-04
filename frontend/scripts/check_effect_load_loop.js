// An effect that starts a load must not depend on the flag that load sets.
//
// ## The fault
//
// A store that fetches keeps two flags: `loaded`, which says the answer is in, and `loading`, which
// says a request is out. A screen that wants the answer writes the obvious thing:
//
//     $effect(() => {
//         if (!people.loaded && !people.loading) void people.load();
//     });
//
// While the server answers, that is correct. When the server refuses it is a loop. The request
// fails, `loading` goes back to false, `loaded` never goes true, and `loading` is a dependency of
// this effect, so the effect runs again, sees the same two flags, and asks again, as fast as the
// machine can go (thousands of requests in a few seconds).
//
// Nothing about it looks wrong, there is no error anywhere, and it only happens when something else
// is already broken, which is the worst possible moment to add a request storm.
//
// ## The rule
//
// Inside an `$effect`, the in-flight flag may be CHECKED but not DEPENDED ON. Reading it inside
// `untrack` is the difference:
//
//     $effect(() => {
//         const stale = !people.loaded;
//         untrack(() => {
//             if (stale && !people.loading) void people.load();
//         });
//     });
//
// `loaded` alone cannot loop: on success it goes true, the effect runs once more and returns; on
// failure nothing it depends on changed, so it is one attempt and no retry. `loading` is still
// checked, so two screens mounted together do not both fetch: it is a guard rather than a
// trigger.
//
// ## What is NOT caught, deliberately
//
// Reading somebody ELSE's in-flight flag is not this. `AssetGrid` re-asks for its page while an
// import is running: it depends on `imports.busy` and calls its own `catchUp`, and nothing it
// calls moves that flag. The loop needs the same object on both sides, so that is what is matched.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

/** The flags a fetch raises while it is out. Not `loaded`, which is the one that may be depended on. */
const IN_FLIGHT = 'loading|busy|saving|pending|refreshing';

/** What starts the work those flags describe. */
const STARTS_WORK = 'load|reload|refresh|fetch|save';

/** The body of every `$effect(...)` / `$effect.pre(...)` in a file, brackets balanced. */
function effectBodies(source) {
	const bodies = [];
	const opener = /\$effect(?:\.pre)?\s*\(/g;
	let found;
	while ((found = opener.exec(source)) !== null) {
		const from = source.indexOf('(', found.index);
		let depth = 0;
		for (let at = from; at < source.length; at += 1) {
			if (source[at] === '(') depth += 1;
			else if (source[at] === ')') {
				depth -= 1;
				if (depth === 0) {
					bodies.push(source.slice(from, at + 1));
					break;
				}
			}
		}
	}
	return bodies;
}

/** The same text with every `untrack(...)` call replaced, so what is left is what is DEPENDED on. */
function tracked(body) {
	let left = body;
	for (;;) {
		const at = left.search(/\buntrack\s*\(/);
		if (at === -1) return left;
		const from = left.indexOf('(', at);
		let depth = 0;
		let closed = -1;
		for (let scan = from; scan < left.length; scan += 1) {
			if (left[scan] === '(') depth += 1;
			else if (left[scan] === ')') {
				depth -= 1;
				if (depth === 0) {
					closed = scan;
					break;
				}
			}
		}
		if (closed === -1) return left;
		left = `${left.slice(0, at)} untracked ${left.slice(closed + 1)}`;
	}
}

/** Comments stripped, so prose about loading is not a read. */
function withoutComments(text) {
	return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}

/**
 * Every `X` whose in-flight flag this effect depends on AND whose work it starts.
 *
 * Both halves on the same receiver. One without the other is not a loop.
 */
function loopsIn(body) {
	const bare = withoutComments(body);
	const depended = new Set();
	const flag = new RegExp(`([A-Za-z_$][\\w$]*)\\.(?:${IN_FLIGHT})\\b`, 'g');
	let found;
	while ((found = flag.exec(tracked(bare))) !== null) depended.add(found[1]);

	const started = new Set();
	const call = new RegExp(`([A-Za-z_$][\\w$]*)\\.(?:${STARTS_WORK})[\\w$]*\\s*\\(`, 'g');
	while ((found = call.exec(bare)) !== null) started.add(found[1]);

	return [...depended].filter((one) => started.has(one));
}

/* The known positive, and the two known negatives.
 *
 * A detector nobody has watched fire is a detector that reports "clean" whether or not it works,
 * and this one is a pile of regular expressions over source text, exactly the kind that stops
 * matching when a rename moves a space. It is cheap to prove here, every run, rather than by
 * somebody remembering to plant a fault. */
function selfTest() {
	const looping = `(() => { if (!people.loaded && !people.loading) void people.load(); })`;
	const guarded = `(() => { const stale = !people.loaded; untrack(() => { if (stale && !people.loading) void people.load(); }); })`;
	const somebodyElses = `(() => { void imports.pulse; if (imports.busy > 0) void untrack(catchUp); })`;

	const wrong = [];
	if (loopsIn(looping).length === 0)
		wrong.push('did not catch an effect that depends on its own load');
	if (loopsIn(guarded).length > 0) wrong.push('flagged a load whose flag is read inside untrack');
	if (loopsIn(somebodyElses).length > 0)
		wrong.push("flagged an effect watching somebody else's flag");
	return wrong;
}

async function everyReactiveFile(dir) {
	const found = [];
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...(await everyReactiveFile(full)));
		else if (entry.name.endsWith('.svelte') || entry.name.endsWith('.svelte.ts')) found.push(full);
	}
	return found;
}

const broken = selfTest();
if (broken.length > 0) {
	console.error(`\neffect-load-loop: this check does not work.\n  ${broken.join('\n  ')}\n`);
	process.exit(1);
}

const files = await everyReactiveFile(SOURCE);

/* A scan that found nothing would pass forever. */
if (files.length < 100) {
	console.error(
		`\neffect-load-loop: only ${files.length} reactive files found under src/.\n` +
			`  That is too few to be right: the tree moved, or this gate is reading the wrong one.\n`
	);
	process.exit(1);
}

const offenders = [];
let effects = 0;

for (const path of files) {
	if (path.endsWith('.test.ts')) continue;
	const where = relative(SOURCE, path).replaceAll('\\', '/');
	for (const body of effectBodies(await readFile(path, 'utf8'))) {
		effects += 1;
		for (const who of loopsIn(body)) offenders.push({ where, who });
	}
}

if (offenders.length > 0) {
	console.error(
		`\nAn effect that starts a load must not depend on the flag that load sets.\n\n` +
			`  ${offenders.length} of them.\n\n` +
			`  While the server answers, this is correct. When the server REFUSES it is a request\n` +
			`  storm: the fetch fails, the in-flight flag goes back to false, the answer flag never\n` +
			`  goes true, and the effect (which depends on the in-flight flag) runs again and asks\n` +
			`  again, as fast as the machine can go.\n\n` +
			`  Depend on the ANSWER flag only, and check the in-flight one inside untrack:\n\n` +
			`      $effect(() => {\n` +
			`          const stale = !people.loaded;\n` +
			`          untrack(() => {\n` +
			`              if (stale && !people.loading) void people.load();\n` +
			`          });\n` +
			`      });\n\n` +
			`  Where they are:\n` +
			offenders.map((one) => `      ${one.where}  (${one.who})`).join('\n') +
			'\n'
	);
	process.exit(1);
}

console.log(`effect-load-loop: ${effects} effects, none depending on a flag its own load sets`);
