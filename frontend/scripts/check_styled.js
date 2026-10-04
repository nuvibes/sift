// A class written into the markup has a rule somewhere.
//
// ## The failure this is for
//
// The dead-CSS gate catches a RULE that reaches no element. This is the other direction: an ELEMENT
// that reaches no rule. Nothing about it looks wrong in either half of the file: the markup says
// `class="pressables"`, the stylesheet is full of other rules, and the component ships as a bare
// `<div>` with no gap, no alignment and no box, while every test passes.
//
// ## Why it is a ratchet
//
// The cases are not one thing. Some classes are genuinely bare. Some are dressed by a PARENT
// reaching in with `:global`, which is how a component styles a snippet its caller wrote:
// correct, and indistinguishable from an oversight without reading both files. And some are dressed
// by an unrelated component's global rule that happens to be in the same bundle, which works and
// should not.
//
// Telling those apart is a judgement per case, which is exactly the situation the other ratchets in
// this folder exist for. So it counts. The number may fall and may never rise, and a new unstyled
// class fails the build on the day it is written.

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { everySvelteFile, fromSource, ratchet, SOURCE } from './lib/tree.js';

/**
 * The written judgement: a class this file writes and ANOTHER file dresses with `:global`.
 *
 * `DRESSED_ELSEWHERE` handles a fixed vocabulary (`frame-body`, `pick-sheet`): names invented
 * for one purpose that could not plausibly mean anything else. It cannot handle `.name`, `.item` or
 * `.divider`, because those are ordinary words: putting them on a list here would excuse every
 * `.name` in the app for ever, including the next one somebody forgets to write a rule for.
 *
 * And that case is real and correct. A component that renders a caller's snippet has to dress it
 * from its own file with `:global`, because a snippet is compiled in the scope of whoever WROTE it:
 * `LabelledRow` styles the label its caller passes, the shared menu styles the rows its caller
 * writes, the player bar styles the divider a caller puts in the drawer. The file writing the class
 * has nothing to say about it, and correctly says nothing.
 *
 * So the comment is the mechanism, in the shape the other gates in this folder use: one line, one
 * fixed spelling, naming the classes it covers.
 *
 *     DRESSED BY: .name (LabelledRow styles the label snippet its caller writes)
 *
 * Per class, not per file, so a file that legitimately hands `.name` to a parent still fails on the
 * next unstyled class somebody adds beside it.
 */
const DRESSED_BY = /DRESSED BY:([^\n]*)/g;

/**
 * Classes a component is HANDED and another file dresses.
 *
 * The app-wide stylesheet is read below rather than listed here, so anything declared in `app.css`
 * (the shared sheet chrome, the menu surfaces, the utilities) counts as styled without needing
 * an entry. What is left is the narrow case a machine cannot tell from an oversight: a class passed
 * INTO a component as a prop, which the component styles from its own file. `viewportClass`,
 * `sheetClass`, `triggerClass`. The receiving component is where the rule belongs, and the file
 * that writes the name legitimately has nothing to say about it.
 *
 * The list is short on purpose. If it grows past a couple of dozen the rule is not being followed.
 */
const DRESSED_ELSEWHERE = new Set([
	'frame-body',
	'main-scroll',
	'add-scroll',
	'bare',
	'entity-fields-sheet',
	'pick-sheet',
	'share-sheet',
	'add-sheet',
	'folder-sheet',
	'menu-wrap'
]);

/* Everything the app-wide stylesheet declares. A class dressed there is dressed, and asking the
   component that uses it to repeat the rule would be asking for a second implementation. */
const appWide = new Set(
	[
		...(await readFile(join(SOURCE, 'app.css'), 'utf8'))
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.matchAll(/\.([A-Za-z][\w-]*)/g)
	].map((one) => one[1])
);

const problems = [];
let scanned = 0;

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	const body = await readFile(path, 'utf8');

	const opens = body.indexOf('<style>');
	if (opens === -1) continue; // no stylesheet at all: every class here is somebody else's
	scanned += 1;

	/* From after the script, or from the top when there is no script at all. `lastIndexOf`
	   answers -1 for a component that has none, and `slice(-1, ...)` counts from the END, so
	   without this the markup read would be the file's last character and every class in it
	   invisible. */
	const afterScript = body.lastIndexOf('</script>');
	const markup = body
		.slice(afterScript === -1 ? 0 : afterScript, opens)
		.replace(/<!--[\s\S]*?-->/g, '');
	const style = body.slice(opens).replace(/\/\*[\s\S]*?\*\//g, '');

	/*
	 * The literal words in a `class` attribute, plus every `class:name` directive.
	 *
	 * The interpolations are stripped rather than the whole attribute being skipped: most class
	 * attributes hold a `{...}`, so skipping them would ignore nearly every class.
	 *
	 * What is inside the braces is deliberately not read: `class={busy ? 'a' : 'b'}` is a value
	 * only the runtime knows, and guessing at it would produce false failures, which is worse than
	 * the misses it would catch.
	 *
	 * An interpolation becomes a marker that cannot occur in a class name, and any token still
	 * holding one is dropped: replacing it with a space would split `class="size-{size}"` into a
	 * bogus class `size-`. `size-{size}` is a name only the runtime knows, exactly like `{busy ?
	 * 'a' : 'b'}`. A separate word beside it (`class="chip {tone}"`) is still read, because
	 * that one IS literal.
	 */
	/* Deliberately NOT a space. A space is eaten by the split below and the stump comes straight
	   back as a bogus class. A control character cannot occur in a class name.
	   Written as an escape rather than typed: a literal NUL byte makes the file BINARY, and every
	   `grep -I` check in the repo (the hygiene gate included) skips a binary file in silence. */
	const RUNTIME = '\u0000';
	const used = new Set();
	for (const match of markup.matchAll(/\sclass="([^"]*)"/g)) {
		for (const one of match[1].replace(/\{[^}]*\}/g, RUNTIME).split(/\s+/)) {
			if (one && !one.includes(RUNTIME)) used.add(one);
		}
	}
	for (const match of markup.matchAll(/class:([A-Za-z][\w-]*)/g)) used.add(match[1]);

	const declared = new Set([...style.matchAll(/\.([A-Za-z][\w-]*)/g)].map((one) => one[1]));

	/* Read from the WHOLE file rather than from the markup, so the line can sit in the component's
	   comment at the top (which is where somebody reads it) rather than beside one element. */
	const byTheParent = new Set();
	for (const line of body.matchAll(DRESSED_BY)) {
		for (const named of line[1].matchAll(/\.([A-Za-z][\w-]*)/g)) byTheParent.add(named[1]);
	}

	const bare = [...used].filter(
		(one) =>
			!declared.has(one) &&
			!appWide.has(one) &&
			!DRESSED_ELSEWHERE.has(one) &&
			!byTheParent.has(one)
	);
	if (bare.length > 0) problems.push({ where, bare });
}

/* A scan that found nothing would pass forever. */
if (scanned < 80) {
	console.error(
		`\nstyled: only ${scanned} components with a stylesheet found under src/.\n` +
			`  That is too few to be right: the tree moved, or this gate is reading the wrong one.\n`
	);
	process.exit(1);
}

const total = problems.reduce((count, one) => count + one.bare.length, 0);

/* The offender list is a file and then its classes under it, flattened into the lines the ratchet
   prints, so a long list still reads as which file owes what. */
const offenders = [];
for (const one of problems) {
	offenders.push(one.where);
	for (const name of one.bare) offenders.push(`  .${name}`);
}

const complaint = await ratchet('styled', 'unstyled', total, {
	what: 'class(es) in the markup with no rule anywhere in the file',
	instead:
		'The dead-CSS gate catches a rule that reaches no element. This is the other way round,\n' +
		'    and it looks like nothing: the markup names a class, the stylesheet is full of other\n' +
		'    rules, and the thing ships as a bare <div> with no gap and no alignment, past every\n' +
		'    other gate and test.\n\n' +
		'    Write the rule. If a PARENT dresses it with `:global` (which is how a component styles\n' +
		'    a snippet its caller wrote), say so in the file, on one line, in this exact spelling,\n' +
		'    naming the class and which component dresses it:\n' +
		'        DRESSED BY: .name (LabelledRow styles the label snippet its caller writes)\n' +
		'    If it is handed IN as a prop to a component that styles it, add it to DRESSED_ELSEWHERE.',
	offenders
});

if (complaint) {
	console.error('\nA class in the markup with no rule anywhere in the file.\n');
	console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(
	`styled: ${scanned} components with a stylesheet; ${total} classes in them have no rule ` +
		`(at the recorded number)`
);
