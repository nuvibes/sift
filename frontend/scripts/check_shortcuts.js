// Keyboard shortcuts the app reads without declaring them, counted, and the count may only fall.
//
// ## The failure this is for
//
// A shortcut written as a comparison against `event.key` inside whichever component is listening,
// plus a hand-written list of what the keys do, drifts: the list says one thing while the code
// takes another, because a list written beside the code is not what changes when the code does.
//
// `$lib/shell/shortcuts` is the one declaration, and this is what stops the app growing a second one. A
// shared thing with no enforcement is a suggestion, and a suggestion loses to whoever is in a
// hurry.
//
// ## Why a ratchet and not a ban
//
// The same reason as the buttons. A gate that refused every undeclared key comparison immediately
// would fail on handlers that are correct and not yet moved, and the only ways to ship that are one
// change nobody can really review or a gate weakened until it passes.
//
// So it counts. The count may fall and may never rise: a new undeclared shortcut fails the build on
// the day it is written, and every migration locks its own progress in permanently.
//
// ## What is counted, and the three things that are deliberately not
//
// **Only app-wide handlers.** A key listener on `window`, `document` or `<svelte:window>` fires
// because you are in Sift, whatever has the focus, which is exactly what a shortcut is, and
// exactly what a list of them has to cover. A handler bound to an ELEMENT only fires while that
// element has the focus: arrow keys moving through a tree, Enter adding a row, Space pressing the
// button you are on. That is the widget's own keyboard behaviour, it is what makes it usable
// without a mouse, and listing it would bury the ten keys somebody wants under forty they already
// expect.
//
// **Escape is not counted.** "Close what is open" is ONE app behaviour that happens to be
// implemented by every panel that can be opened. It is declared once, as `app.dismiss`, and a
// handler that acts on nothing else is that behaviour rather than a second shortcut.
//
// **A handler that never reads which key was pressed is not counted.** The screen bar wakes on any
// keystroke at all; that is an activity signal, not a shortcut, and it has nothing to declare.
//
// And by location: `lib/components/common/` is where the primitives live, for the same reason the
// hand-rolled gate leaves them alone: a primitive IS the keyboard behaviour, and counting it
// would make the target unreachable and the number meaningless.

import { readFile } from 'node:fs/promises';

import { everyFile, fromSource, ratchet, recorded, SOURCE } from './lib/tree.js';

/** Where a component IS the keyboard behaviour rather than reaching past it. */
const PRIMITIVES = 'lib/components/common/';

/*
 * The design gallery, and the prototypes that live beside it.
 *
 * A prefix rather than a list of files: a list somebody has to remember to extend goes red on the
 * next prototype, and the easy answer (dressing the prototype to please the gate) ruins the
 * proposal. Everything under `routes/design/` exists to draw controls for comparison, so that is
 * what is named.
 */
const DRAWN_ON_PURPOSE = 'routes/design/';

/**
 * The key that is declared once for the whole application.
 *
 * A handler that compares nothing else is the dismissal behaviour, which has one row in the list
 * and does not want fifteen.
 */
const DECLARED_ONCE = new Set(['Escape']);

/**
 * The elements that ARE the application-wide reach, in markup.
 *
 * Scoped to the tag rather than matched anywhere in the file, and that distinction is the whole
 * gate. `onkeydown={handler}` reads identically on `<svelte:window>` and on a `<button>`; counting
 * both would report a tile's Enter and a combobox's arrow keys as undeclared shortcuts when they
 * are the widget's own keyboard behaviour and must stay there.
 */
const APP_WIDE_TAG = /<svelte:(?:window|document|body)\b[^>]*>/g;

/** How an app-wide listener names the function that will read the press. */
const HANDLER_ATTRIBUTE = /on(?:keydown|keyup|keydowncapture|keyupcapture)=\{([A-Za-z_$][\w$]*)\}/g;

/**
 * The same binding written in Svelte's shorthand: `<svelte:window {onkeydown} />`.
 *
 * Without this, a new undeclared key in a component using the shorthand is not caught: no handler
 * name comes out of the tag, so nothing is read, and it does not even report itself as unreadable:
 * silence and success are the same answer here.
 */
const HANDLER_SHORTHAND = /\{(on(?:keydown|keyup|keydowncapture|keyupcapture))\}/g;

/** The same reach written in script: `window.addEventListener('keydown', handler)`. */
const HANDLER_LISTENER =
	/(?:window|document)\.addEventListener\(\s*['"]key(?:down|up)['"]\s*,\s*([A-Za-z_$][\w$]*)/g;

/**
 * Every literal key a piece of code compares against.
 *
 * Deliberately several spellings, because all of them are in the tree: an `===` against `event.key`,
 * a `case` inside a switch on it, and a pattern tested against it. A comparison this does not
 * recognise is a shortcut that escapes the count, so it is better to be broad here and let the
 * exemptions above do the narrowing.
 */
function keysComparedIn(body) {
	const found = new Set();
	for (const [, key] of body.matchAll(/\.key\s*(?:===|!==|==|!=)\s*['"]([^'"]*)['"]/g)) {
		found.add(key);
	}
	for (const [, key] of body.matchAll(/['"]([^'"]*)['"]\s*(?:===|!==|==|!=)\s*[\w.]*\.key\b/g)) {
		found.add(key);
	}
	if (/\.key\s*\)/.test(body) && /\/\^?\[/.test(body)) found.add('(a pattern)');
	for (const [, key] of body.matchAll(/\bcase\s+['"]([^'"]*)['"]\s*:/g)) found.add(key);
	return found;
}

/**
 * The body of the named function, or null when it cannot be found.
 *
 * Braces are counted from the opening one rather than the indentation being trusted: a handler can
 * be an arrow function assigned to a const, and those are not indented like a declaration. Returning
 * null rather than guessing matters. See `unreadable` below, which refuses rather than passing.
 */
function bodyOf(source, name) {
	const start = source.search(
		new RegExp(`(?:function\\s+${name}\\s*\\(|(?:const|let)\\s+${name}\\s*=\\s*(?:async\\s*)?\\()`)
	);
	if (start === -1) return null;
	const open = source.indexOf('{', start);
	if (open === -1) return null;
	let depth = 0;
	for (let at = open; at < source.length; at += 1) {
		if (source[at] === '{') depth += 1;
		else if (source[at] === '}') {
			depth -= 1;
			if (depth === 0) return source.slice(open, at + 1);
		}
	}
	return null;
}

/*
 * A shortcut this gate MUST see, checked before it is allowed to look at the real tree.
 *
 * The count starts at zero, which is the happy answer and is also the answer a broken check gives.
 * Every assertion below is about finding NOTHING, so a `keysComparedIn` that quietly stopped
 * recognising a comparison (a spelling changed, a pattern edited) would report a clean tree for
 * ever and nobody would know. So the rules are run over a sample that is known to break them, and
 * this refuses to run at all unless it catches every part of it.
 *
 * Each entry names the shape it is standing in for, because a fixture nobody can read is a fixture
 * somebody deletes.
 */
const KNOWN_POSITIVE = `
<script>
	function planted(event) {
		if (event.key === 'q') act();
		if ('w' === event.key) act();
		switch (event.key) {
			case 'e':
				act();
		}
		if (event.key === 'Escape') shut();
	}
	function shorthanded(event) {
		if (event.key === 'r') act();
	}
</script>
<svelte:window onkeydown={planted} />
<svelte:document {onkeydown} />
`;

/**
 * Every app-wide key handler this source binds, by name.
 *
 * A function rather than a loop, so the known positive below runs the code the tree runs: a
 * self-check that matched the patterns directly would stay green with a pattern dropped from the
 * collection.
 */
function handlersIn(source) {
	const handlers = new Set();
	for (const [tag] of source.matchAll(APP_WIDE_TAG)) {
		for (const [, name] of tag.matchAll(HANDLER_ATTRIBUTE)) handlers.add(name);
		for (const [, name] of tag.matchAll(HANDLER_SHORTHAND)) handlers.add(name);
	}
	for (const [, name] of source.matchAll(HANDLER_LISTENER)) handlers.add(name);
	return handlers;
}

function proveTheRulesStillWork() {
	const body = bodyOf(KNOWN_POSITIVE, 'planted');
	if (body === null) {
		throw new Error("the known positive's handler could not be found: bodyOf is broken");
	}
	const seen = keysComparedIn(body);
	// One per spelling that is really in the tree: an `===` either way round, and a `case`.
	for (const key of ['q', 'w', 'e']) {
		if (!seen.has(key)) {
			throw new Error(`the known positive's ${key} was not seen: keysComparedIn is broken`);
		}
	}
	// And the one that must NOT be counted, or every panel in the app becomes a shortcut.
	if (![...seen].filter((key) => !DECLARED_ONCE.has(key)).length) {
		throw new Error('the known positive counted nothing at all');
	}
	if (!seen.has('Escape')) {
		throw new Error('Escape was not even seen: the exemption is doing nothing');
	}
	// Through the real collection, not the patterns on their own (see `handlersIn`). Both
	// spellings, because the shorthand could be dropped from the loop while every pattern still
	// matched perfectly well on its own.
	const handlers = handlersIn(KNOWN_POSITIVE);
	for (const name of ['planted', 'onkeydown']) {
		if (!handlers.has(name)) {
			throw new Error(`the known positive's ${name} binding was not seen: it is unwatched`);
		}
	}
}

proveTheRulesStillWork();

const undeclared = [];
const unreadable = [];

for (const path of await everyFile(SOURCE, ['.svelte', '.ts'])) {
	const where = fromSource(path);
	if (where.includes(PRIMITIVES) || where.startsWith(DRAWN_ON_PURPOSE)) continue;
	if (where.endsWith('.test.ts') || where.endsWith('.svelte.test.ts')) continue;
	if (where === 'lib/shell/shortcuts.ts') continue;

	const source = await readFile(path, 'utf8');
	for (const name of handlersIn(source)) {
		const body = bodyOf(source, name);
		if (body === null) {
			// An inline arrow bound straight into the markup, or a shape this cannot read. Reported
			// rather than skipped: a handler this gate cannot see is a shortcut it is not counting,
			// and silence would read as coverage.
			unreadable.push(`${where}: ${name}`);
			continue;
		}
		const compared = [...keysComparedIn(body)].filter((key) => !DECLARED_ONCE.has(key));
		for (const key of compared) undeclared.push(`${where}: ${name} reads ${key}`);
	}
}

const found = undeclared.length;

if (unreadable.length > 0) {
	console.error('These app-wide key handlers could not be read, so nothing is counting them:');
	for (const one of unreadable.sort()) console.error(`  ${one}`);
	console.error(
		'\nGive the handler a name this can find, or declare its keys in $lib/shell/shortcuts.'
	);
	process.exit(1);
}

const complaint = await ratchet('shortcuts', 'undeclared', found, {
	what: 'undeclared app-wide shortcut(s)',
	instead:
		'Each of these reads a key the shortcut list does not know about. Declare it in\n' +
		'    $lib/shell/shortcuts and ask matches(event, id) instead of comparing the key here.',
	offenders: undeclared.sort()
});

if (complaint) {
	console.error(complaint);
	process.exit(1);
}

console.log(
	`Undeclared app-wide shortcuts: ${found} (baseline ${(await recorded('shortcuts')).undeclared}).`
);
if (found > 0) {
	for (const one of undeclared.sort()) console.log(`  still to declare: ${one}`);
}
