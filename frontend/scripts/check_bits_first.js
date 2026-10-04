// A shared component either comes from the headless library, or says in writing why it does not,
// and either way it is drawn on the gallery.
//
// ## The rule this enforces
//
// Sift builds its interface on a headless component library: things with real keyboard, focus and
// screen-reader behaviour (menus, dialogs, selects, switches) come from there, because that
// behaviour is genuinely hard and getting it subtly wrong is invisible to whoever writes it and
// total for whoever needs it.
//
// But "use the library" cannot be the whole rule, because the library does not have everything. It
// only ships what has behaviour worth sharing, so a spinner, a badge, a skeleton and a breadcrumb
// trail are not in it and never will be: those are shape and colour, and shape and colour are
// this app's own. Building them here is correct. Building a menu here would not be.
//
// The difference between those two cases is a JUDGEMENT, and a judgement that is not written down
// is indistinguishable from not having made one. So:
//
//   1. Every shared component imports the library, or carries a `WHY NOT BITS-UI:` line saying why
//      it does not. One line, in the file, where the next person to open it will read it.
//   2. Every shared component appears on the gallery, so the judgement can be LOOKED at rather than
//      taken on trust. A deviation nobody can see is a deviation nobody can review.
//
// ## Why the second half is a ratchet
//
// The same reason `check_handrolled.js` is one, and it is worth repeating rather than cross-
// referencing: a gate that fails on eighteen files the day it is written gets weakened until it
// passes, and a weakened gate is worse than none because it reports success. So the number of
// primitives missing from the gallery is recorded, may fall, and may never rise. A new primitive
// that skips the gallery fails the build on the day it is written.
//
// The first half is NOT a ratchet. It is one line per file, it can be satisfied in the same minute
// the file is written, and there is no honest reason to owe it.

import { existsSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { ratchet, SOURCE } from './lib/tree.js';

/**
 * Where the shared components live.
 *
 * The population is every shared component under `lib`, minus the screens below, not only
 * `lib/components/common`, because a component put anywhere else would escape the rule, and the
 * promise is that every part of the interface can be looked at in one place.
 */
const PRIMITIVES = 'lib/components/common';
const SHARED = 'lib';
/** The one page that draws every component on purpose. */
const GALLERY = 'routes/design/+page.svelte';

/** The written judgement. Deliberately shouty and deliberately one fixed spelling, so it greps. */
const EXEMPTION = 'WHY NOT BITS-UI:';

/*
 * The other written judgement: a component that DRAWS NOTHING of its own.
 *
 * Two of them, and both are real. `FileVerbs` is a host: it owns the actions and the sheets that
 * ask before they write, and hands them to a snippet; it renders no element at all, and what it
 * produces (the verb buttons, the verb menu rows) is on the gallery already. `Toaster` is a
 * singleton that lives in the layout, so importing it here would put a SECOND one on the page, and
 * the toasts it draws are already shown by the section that fires one.
 *
 * This is a hole in the rule, so it is a hole you have to write your name in. One line, in the file,
 * in this exact spelling, the same shape as the reason above, for the same reason: a judgement
 * nobody can see is indistinguishable from not having made one. It is deliberately NOT a list of
 * filenames in this script, because a list here is a thing you edit while looking at the gate rather
 * than while looking at the component.
 */
const DRAWS_NOTHING = 'NOT ON THE GALLERY:';

/* Files in the primitives folder that are not components. A gate that demanded a gallery entry for
   a helper module would be asking for a picture of a function. */
const NOT_A_COMPONENT = /\.(ts|js)$/;

/*
 * What is a SCREEN rather than a part, and so is not owed a gallery entry.
 *
 * The line is: does drawing this on the gallery show you a piece of the interface, or does it show
 * you the app again? A chip, a row, a panel and an empty state are pieces. A settings pane, the
 * asset grid and the theater wall are the app, and a gallery entry for one is a screenshot of a
 * route you can already visit, which is worse than nothing, because it is a second copy of that
 * screen that can drift from the real one.
 *
 * Written as PREFIXES rather than filenames, so a new settings pane or a new player part is covered
 * the day it is written instead of failing a gate nobody expected. Every one of these is a folder
 * whose whole contents are screens; anything genuinely shared is moved out of them, as
 * the rows are.
 */
const SCREENS = [
	/* Whole routes and the panes they are made of. A settings pane is a page of a screen. */
	'lib/settings-ui/',
	/* The library, jobs and theater screens: each is a route's entire contents. */
	'lib/library/',
	'lib/jobs/',
	'lib/components/theater/',
	/* The Insights, recap and Your path screens: their blocks, cards and lists are the pieces of one
	   screen that reads the server's figures, not primitives to draw beside a chip. */
	'lib/components/insights/',
	/* The player. A media stage with a real element in it is not a thing to draw beside a chip, and
	   its parts only mean anything with a file loaded. */
	'lib/components/player/',
	/* The queues that each fill a whole screen with one question. */
	'lib/components/organize/',
	/* The cookies sheet, which is the same shape as those: one screen asking one question, opened
	   from three doors. It lives under `lib` rather than under its route precisely because three
	   routes open it, and that is not the same thing as being a PIECE. A gallery entry for it
	   would be a second copy of a screen you can reach by pressing Cookies. */
	'lib/components/downloads/',
	'lib/components/faces/',
	'lib/components/suggestions/',
	/* The one dynamic host and the walls it draws. */
	'lib/components/AssetGrid.svelte',
	'lib/components/AssetModal.svelte',
	'lib/components/AssetView.svelte'
];

/* Test-only harnesses. Not interface at all (they exist so a component test has something to
   mount), so asking for a picture of one is asking for a picture of a fixture. */
const HARNESSES = /(Harness|Probe)\.svelte$/;

/** Every `.svelte` under `lib`, as a path relative to `src`, exemptions included. */
const everySvelteFile = [];

/* Its own walk rather than tree.js's: this one also has to record every `.svelte` it passes (for the
   exemption-names-nothing check below) and to drop test harnesses and `.test.svelte` fixtures. */
/** Every shared component, as a path relative to `src`. */
async function listShared() {
	const found = [];
	const walk = async (dir) => {
		for (const entry of await readdir(dir, { withFileTypes: true })) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				await walk(full);
				continue;
			}
			if (!entry.name.endsWith('.svelte')) continue;
			if (entry.name.endsWith('.test.svelte')) continue;
			if (HARNESSES.test(entry.name)) continue;
			if (NOT_A_COMPONENT.test(entry.name)) continue;
			const where = full.slice(SOURCE.length + 1).replaceAll('\\', '/');
			everySvelteFile.push(where);
			if (SCREENS.some((prefix) => where.startsWith(prefix))) continue;
			found.push(where);
		}
	};
	await walk(join(SOURCE, SHARED));
	return found.sort();
}

/*
 * Everything the gallery pulls in, however deep.
 *
 * Transitive on purpose. A primitive drawn INSIDE another component that the gallery shows is on
 * the gallery (the chip inside the tag editor is visible whether or not the page names it), and
 * a gate that only read the page's own import list would demand a duplicate entry for it. Following
 * the imports is also the only version that cannot be satisfied by importing something and never
 * rendering it, because an unrendered import is dead code and a different gate takes that.
 */
async function reachableFromGallery() {
	const seen = new Set();
	const queue = [join(SOURCE, GALLERY)];

	while (queue.length > 0) {
		const path = queue.pop();
		if (seen.has(path)) continue;
		seen.add(path);

		let source;
		try {
			source = await readFile(path, 'utf8');
		} catch {
			continue; // an import that does not resolve to a file on disk: a package, or a type-only path
		}

		for (const match of source.matchAll(/from\s+'(\$lib\/[^']+)'/g)) {
			const target = join(SOURCE, 'lib', match[1].slice('$lib/'.length));
			for (const candidate of [
				target,
				`${target}.svelte`,
				`${target}.ts`,
				join(target, 'index.ts')
			]) {
				queue.push(candidate);
			}
		}
	}
	return seen;
}

const primitives = await listShared();
/*
 * THE GALLERY MAY NOT BE HERE. Its source is a separate repository (see .gitignore at the root),
 * nested at routes/design/ where it is present; in a clone of this repository that folder does not
 * exist. The written-reason half runs everywhere; the drawn-on-the-gallery half runs only where the
 * gallery is, and says so rather than passing in silence.
 */
const galleryHere = existsSync(join(SOURCE, GALLERY));
const reachable = galleryHere ? await reachableFromGallery() : null;

/* A scan that found nothing would pass, silently, forever. There are dozens of primitives; if
   this finds a handful, the folder moved and the gate is reading an empty room. */
if (primitives.length < 20) {
	console.error(
		`\nbits-first: found only ${primitives.length} shared components in src/${PRIMITIVES}.\n` +
			`  That is too few to be right: the folder has moved, or this gate is reading the wrong one.\n`
	);
	process.exit(1);
}

const unexplained = [];
const missingFromGallery = [];

for (const where of primitives) {
	const path = join(SOURCE, where);
	const source = await readFile(path, 'utf8');

	/* The written-reason half applies to the PRIMITIVES only. A composition is made of them and its
	   behaviour comes from whatever it is made of, so asking a person's identity band whether it
	   should have been a bits-ui component is asking a question with no answer. */
	if (where.startsWith(`${PRIMITIVES}/`)) {
		const usesLibrary = /from\s+'bits-ui'/.test(source);
		if (!usesLibrary && !source.includes(EXEMPTION)) unexplained.push(where);
	}

	if (reachable && !reachable.has(path) && !source.includes(DRAWS_NOTHING))
		missingFromGallery.push(where);
}

const complaints = [];

if (unexplained.length > 0) {
	complaints.push(
		`${unexplained.length} shared component(s) neither use bits-ui nor say why not.\n` +
			`    Add ONE line near the top of the component's comment, in this exact spelling:\n` +
			`      ${EXEMPTION} <the reason>\n` +
			`    A real reason is "bits-ui has no such primitive: this is shape and colour only" or\n` +
			`    "the site element already does all of it". "It was quicker" is not one; if the\n` +
			`    library has the behaviour, use the library.\n` +
			`    Which ones:\n${unexplained.map((one) => `      ${one}`).join('\n')}`
	);
}

const now = missingFromGallery.length;

if (galleryHere) {
	const absent = await ratchet('bits-first', 'absentFromGallery', now, {
		what: 'shared component(s) not reachable from the gallery',
		instead:
			`Draw it on src/${GALLERY}, in the section it belongs to, using the REAL component.\n` +
			`    A gallery that redraws its own version of a thing is one more implementation of it.\n` +
			`    If it genuinely draws nothing (a host that only hands things to a snippet, or a\n` +
			`    singleton the layout already renders), say so in the file, in this exact spelling:\n` +
			`      ${DRAWS_NOTHING} <the reason>`,
		offenders: missingFromGallery
	});
	if (absent) complaints.push(absent);
}

/*
 * AN EXEMPTION THAT NAMES NOTHING IS AN EXEMPTION NOBODY CAN SEE IS WRONG.
 *
 * `SCREENS` takes whole directories out of everything above, so widening it is the one edit here
 * that cannot fail: a prefix that is too broad, or one left behind by a rename, simply takes more
 * out and the gate goes on reporting green over components it has stopped reading. That is more
 * dangerous than adding a rule, because there is nothing to notice.
 *
 * So each prefix has to match something. It does not prove the prefix is narrow enough (nothing
 * can), but it does end the case where a directory was renamed and the exemption stayed, which is
 * how these lists rot. The `position: fixed` list is held to the same rule for the same reason.
 */
const namingNothing = SCREENS.filter(
	(prefix) => !everySvelteFile.some((where) => where.startsWith(prefix))
);
if (namingNothing.length > 0) {
	complaints.push(
		`these screens are excused and there is nothing there to excuse:\n` +
			namingNothing.map((one) => `      ${one}`).join('\n') +
			`\n    A prefix that matches no file was renamed or deleted. Take it out.`
	);
}

if (complaints.length > 0) {
	console.error('\nbits-ui first, or say why not, and draw it either way.\n');
	for (const complaint of complaints) console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(
	`bits-first: ${primitives.length} shared components, all accounted for; ` +
		(galleryHere
			? `${now} not yet on the gallery (at the recorded number)`
			: 'the gallery is not in this tree, so its half was not measured')
);
