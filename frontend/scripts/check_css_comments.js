// A comment in a stylesheet that never closes, and the rules it eats.
//
// ## The failure this is for
//
// CSS comments do not nest. `/*` opens one and the very first `*/` closes it, whatever is written
// in between. So a comment whose terminator is missing does not end at the end of its paragraph.
// It runs on until the next `*/` anywhere in the file, and everything it passes over stops being
// stylesheet. Rules, whole blocks, the opening of the next comment: all of it becomes prose, and a
// fixed size quietly disappears from the screen.
//
// ## Why nothing else catches it
//
// Every other check in this directory reads a stylesheet by first stripping comments with the same
// `/\*[\s\S]*?\*\//` that CSS itself applies. They therefore agree with the browser about what was
// swallowed, and a rule that is inside a comment is simply not a rule any of them can see. The
// dead-CSS check does not report it (there is no selector), the styled check does not report it
// (there is no class to be undressed), and neither does Svelte, which is right: it is a comment.
//
// Prettier does not repair it either: an unterminated comment is valid CSS, so there is nothing
// to reformat.
//
// ## What it reports
//
//   1. **An unterminated comment.** A `/*` in a `<style>` block with no `*/` after it.
//   2. **A `/*` INSIDE a comment.** Harmless to the browser (it is already inside a comment), and
//      an exact fingerprint of a terminator that was lost, because the sequence that follows a
//      missing `*/` is always the next paragraph's own opening.
//   3. **A `*/` that closes nothing**, which is the same fault from the other end and the one that
//      is easiest to write by hand: a comment about comments, quoting `* /` in its own prose, ends
//      two lines early and leaves the rest of its own sentence in the stylesheet as garbage.

import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

async function everySvelteFile(dir) {
	const found = [];
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...(await everySvelteFile(full)));
		else if (entry.name.endsWith('.svelte')) found.push(full);
	}
	return found;
}

/** Which line of the whole file an offset falls on, counting from one. */
function lineAt(body, offset) {
	return body.slice(0, offset).split('\n').length;
}

const problems = [];
let scanned = 0;

for (const path of [...(await everySvelteFile(SOURCE))].sort()) {
	const where = relative(SOURCE, path).replaceAll('\\', '/');
	const body = await readFile(path, 'utf8');

	const opens = body.indexOf('<style>');
	if (opens === -1) continue;
	scanned += 1;

	/* The stylesheet only. A `/*` in the script above it is JavaScript, where comments follow other
	   rules entirely: one inside a string or a regular expression is not a comment at all. */
	const style = body.slice(opens);

	let at = 0;
	while (at < style.length) {
		const start = style.indexOf('/*', at);
		/* Nothing left to OPEN, so anything that still closes is closing nothing. Checked here as
		   well as inside the loop, or a file whose only fault is a stray terminator after its last
		   comment would walk out of the loop unexamined. */
		if (start === -1) {
			const orphan = style.indexOf('*/', at);
			if (orphan !== -1) {
				problems.push({
					where,
					line: lineAt(body, opens + orphan),
					what: 'a terminator that closes nothing: a comment above it ended early',
					text: style.slice(orphan, orphan + 72).split('\n')[0]
				});
			}
			break;
		}
		/* Between where the last comment ended and where this one begins is stylesheet, and a
		   terminator there closes nothing. It is what a comment quoting one in its own prose leaves
		   behind: the comment ends at the quotation and the rest of the sentence is left as CSS. */
		const orphan = style.slice(at, start).indexOf('*/');
		if (orphan !== -1) {
			problems.push({
				where,
				line: lineAt(body, opens + at + orphan),
				what: 'a terminator that closes nothing: a comment above it ended early',
				text: style.slice(at + orphan, at + orphan + 72).split('\n')[0]
			});
		}
		const end = style.indexOf('*/', start + 2);
		if (end === -1) {
			problems.push({
				where,
				line: lineAt(body, opens + start),
				what: 'never closed',
				text: style.slice(start, start + 72).split('\n')[0]
			});
			break;
		}
		const nested = style.indexOf('/*', start + 2);
		if (nested !== -1 && nested < end) {
			problems.push({
				where,
				line: lineAt(body, opens + nested),
				what: 'opened inside a comment: the one before it lost its terminator',
				text: style.slice(nested, nested + 72).split('\n')[0]
			});
		}
		at = end + 2;
	}
}

/* The same floor every check here carries: a number this far below the real one means the tree moved
   and this walked an empty directory, which would pass in silence. */
if (scanned < 80) {
	console.error(
		`\ncss-comments: only ${scanned} components with a stylesheet found under src/.\n` +
			`  That is too few to be right: the tree moved, or this check is reading the wrong one.\n`
	);
	process.exit(1);
}

if (problems.length > 0) {
	console.error('\nA comment in a stylesheet that does not close where it looks like it closes.\n');
	console.error(
		'  CSS comments do not nest: `/*` runs to the FIRST `*/`, so a missing terminator swallows\n' +
			'  every rule after it until the next one, silently, and with nothing left for the\n' +
			'  dead-CSS or styled checks to see, because a rule inside a comment is not a rule.\n\n' +
			'  Close the comment where it was meant to end.\n'
	);
	for (const one of problems) {
		console.error(`    ${one.where}:${one.line}  ${one.what}`);
		console.error(`      ${one.text}`);
	}
	console.error('');
	process.exit(1);
}

console.log(`css-comments: ${scanned} stylesheets checked; every comment closes where it opens`);
