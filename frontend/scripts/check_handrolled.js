// A hand-rolled copy of something the interface already has, counted, and the count may only
// fall.
//
// ## The failure this is for
//
// A shared component with no enforcement is a suggestion, and a suggestion loses to whoever is in a
// hurry: the app goes on producing hand-rolled buttons beside the shared one. The rule needs a
// machine behind it or it decays back to where it started.
//
// ## Why a ratchet and not a ban
//
// A gate that refused every hand-rolled button on the day it was written would fail on dozens of
// files. The only ways to ship that are to convert them all in one change (a rewrite touching
// most of the interface at once) or to weaken the gate until it passes, which is a gate that
// lies.
//
// So it counts instead. The count may fall and may never rise. A new hand-rolled button fails the
// build on the day it is written, and every conversion locks its own progress in permanently.
//
// ## Why it also fails when the count DROPS
//
// A ceiling would let the number sit above the truth, and then the next few hand-rolled buttons are
// free. Requiring the baseline to be lowered when work is done is what makes it a ratchet: the
// recorded number is always the real one, so the next regression is caught immediately.
//
// ## What is deliberately NOT counted
//
// Anything in `lib/components/common/`. That is where the primitives live, and a primitive is built
// ON the platform element: the shared button is a real `<button>`, and so are the chip, the menu
// item and the switch, because type, form, keyboard and screen-reader behaviour should be the
// platform's rather than something rebuilt in JavaScript.
//
// The gallery is not counted either: it draws every component on purpose, and so do the prototypes
// beside it. See DRAWN_ON_PURPOSE.

import { readFile } from 'node:fs/promises';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

/** Where primitives are allowed to be built on the site's own elements. */
const PRIMITIVES = 'lib/components/common/';
/**
 * The pages that draw the interface ON PURPOSE, and so are not counted.
 *
 * The gallery, for the reason it is not counted as a user anywhere else: it draws every component
 * deliberately, so counting it would add one to every number here permanently and invisibly.
 *
 * And the bar prototype, which is the same argument one step further. It exists to draw the screen
 * bar's CURRENT shape beside two proposals, so that what differs between them can be looked at,
 * and the current shape is a row of hand-rolled glyphs, which is most of what the proposal is
 * about. Drawing that row with the shared button would be a prototype of something the app does not
 * do, which is worse than not having one.
 */
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
 * The written judgement, for the handful of cases where the platform element really is the answer.
 *
 * An escape hatch is how a ratchet becomes a formality: every file gets a line saying why it is
 * special, the number stops falling, and the gate reports success over a codebase that never
 * converted anything. That risk is the reason for the shape below.
 *
 * Some of the count is not convertible, and three shapes keep coming up:
 *
 *   - A surface that IS the control. A tile is a picture you press; wrapping it in a button that
 *     draws a border, a background and a focus ring around a 200px image is the shared button being
 *     fought.
 *   - A control the browser owns. A `<summary>`, a label acting as a switch, the native fullscreen
 *     affordance: replacing one with a styled button loses the behaviour and keeps the look.
 *   - A test-only harness. There is no interface here to make consistent.
 *
 * Leaving those silently in the count means the number can never reach zero and nothing says which
 * of the remaining instances are the hard ones.
 *
 * ## The shape, which is what keeps it honest
 *
 * One line, in the file, in this exact spelling (`WHY NOT SHARED: button: <the reason>` for the
 * button rule), naming WHICH rule it answers. It exempts the whole file from THAT rule, and that rule only: a
 * tile that is itself the control is excused its raw <button>, and a raw <input> written beside it
 * is still counted. Per rule and per file rather than per instance, which would be a comment above
 * every button. The number of (file, rule) exemptions is itself a ratchet. See `excused` below.
 *
 * It is deliberately not a list of filenames in this script: a list here is edited while looking at
 * the gate, and a line in the file is written while looking at the component.
 */
const SITE_IS_THE_ANSWER = 'WHY NOT SHARED:';
/** The marker with its rule: `WHY NOT SHARED: button: the tile IS the control`. */
const EXCUSES = /WHY NOT SHARED:\s*([a-z-]+):/g;

/**
 * What is counted, and what to do instead.
 *
 * `find` returns how many instances a file holds. A file with four hand-rolled buttons is four, not
 * one: converting three of them is real progress and the number should say so.
 */
const RULES = [
	{
		id: 'button',
		what: 'a hand-rolled <button>',
		instead: "import Button from '$lib/components/common/Button.svelte'",
		find: (source) => source.match(/<button[\s>]/g)?.length ?? 0
	},
	{
		id: 'slider',
		what: 'a bare <input type="range">',
		instead: "import { Slider } from '$lib/components/common'",
		// The attribute, not the tag: an input is written with its attributes on separate lines here,
		// so matching the opening tag would need the whole element. `type="range"` is the one string
		// that means "this is a slider" wherever it appears in the markup.
		find: (source) => source.match(/type=["']range["']/g)?.length ?? 0
	},
	{
		id: 'quiet',
		what: 'a hand-declared .quiet rule',
		instead: 'the `.quiet` utility in app.css already dresses the class: delete the local rule',
		// A local `color: var(--sift-ink-3)` under this name is a second opinion about the
		// face. The utility is the one rule.
		find: (source) => source.match(/^\s*\.quiet(?![\w-])[^{]*\{/gm)?.length ?? 0
	},
	{
		id: 'empty',
		what: 'a hand-declared empty, nothing, blank or waiting rule',
		instead:
			"import Empty from '$lib/components/common/Empty.svelte' (quiet, or busy for an operation in flight)",
		// The empty state and the in-flight line, written per screen under four names.
		find: (source) =>
			source.match(/^\s*\.(empty|nothing|blank|waiting)(?![\w-])[^{]*\{/gm)?.length ?? 0
	},
	{
		id: 'empty-line',
		what: 'a bare <p> standing in for an empty state or an in-flight line',
		instead:
			'import Empty from \'$lib/components/common/Empty.svelte\'; a failure is Problem, a quiet sentence is class="quiet"',
		// The markup, not the rule: `<p class="empty">Nothing here</p>` is the line the component
		// replaces, whether or not the file also dressed it.
		find: (source) => source.match(/<p class="(empty|nothing|blank|none|waiting)"/g)?.length ?? 0
	},
	{
		id: 'input',
		what: 'a bare <input>',
		instead:
			"import { TextInput, Checkbox, ChoiceGroup, ChooseFile, Slider } from '$lib/components/common', one of them is the box",
		// Every kind: a text box is TextInput, a tick is Checkbox, one-of-a-few is ChoiceGroup, a
		// file is ChooseFile, a range is Slider. The tag alone cannot say which, so all are counted.
		find: (source) => source.match(/<input[\s>]/g)?.length ?? 0
	},
	{
		id: 'textarea',
		what: 'a bare <textarea>',
		instead: "import { TextArea } from '$lib/components/common'",
		find: (source) => source.match(/<textarea[\s>]/g)?.length ?? 0
	},
	{
		id: 'heading',
		what: 'a hand-written heading element (<h1> to <h6>)',
		instead:
			"import { SectionHeading } from '$lib/components/common' (over a group; `band` for a heading inside a page)",
		// The element, not its rule: a heading written by hand picks one of several faces and inks,
		// and the eye reads a page's structure from its headings.
		find: (source) => source.match(/<h[1-6][\s>]/g)?.length ?? 0
	},
	{
		id: 'chip',
		what: 'a hand-declared .chip rule',
		instead: "import Chip from '$lib/components/common/Chip.svelte'",
		// The CSS rule, not the class in the markup: a file using the shared component still writes
		// `class="chip"` nowhere, but one styling its own writes the selector.
		find: (source) => source.match(/^\s*\.chips?(?![\w-])[^{]*\{/gm)?.length ?? 0
	}
];

const counts = Object.fromEntries(RULES.map((rule) => [rule.id, 0]));
const offenders = Object.fromEntries(RULES.map((rule) => [rule.id, []]));
/* Counted and ratcheted rather than merely skipped. An exemption nobody can see the size of is how
   this stops being a ratchet: the number of (file, rule) exemptions is recorded and may only fall. */
const excused = [];
/* A marker that names no rule, or a rule this gate does not have. A marker without a rule is
   refused rather than tolerated, because a whole-file excuse would license everything in the
   file. */
const malformed = [];

for (const path of await everySvelteFile(SOURCE)) {
	const where = fromSource(path);
	if (where.startsWith(PRIMITIVES) || where.startsWith(DRAWN_ON_PURPOSE)) continue;

	const source = await readFile(path, 'utf8');
	// The judgement is read from the WHOLE file, because it is written as a comment. Everything
	// below is read from the code alone. See `withoutComments`.
	const excusedRules = new Set();
	if (source.includes(SITE_IS_THE_ANSWER)) {
		const named = [...source.matchAll(EXCUSES)].map((one) => one[1]);
		const markers = source.split(SITE_IS_THE_ANSWER).length - 1;
		if (named.length !== markers || named.some((id) => !RULES.some((rule) => rule.id === id))) {
			malformed.push(where);
		}
		for (const id of named) {
			excusedRules.add(id);
			excused.push(`${where} (${id})`);
		}
	}
	const code = withoutComments(source);
	for (const rule of RULES) {
		if (excusedRules.has(rule.id)) continue;
		const found = rule.find(code);
		if (found === 0) continue;
		counts[rule.id] += found;
		offenders[rule.id].push({ where, found });
	}
}

const complaints = [];

if (malformed.length > 0) {
	complaints.push(
		`${malformed.length} file(s) carry a ${SITE_IS_THE_ANSWER} line that names no rule, or a rule this gate does not have.\n` +
			`    Write it as: ${SITE_IS_THE_ANSWER} <rule>: <the reason>, where <rule> is one of ${RULES.map((rule) => rule.id).join(', ')}.\n` +
			`    Which ones:\n${malformed.map((one) => `      ${one}`).join('\n')}`
	);
}

const excusedFell = await ratchet('handrolled', 'excused', excused.length, {
	what: 'a (file, rule) exemption written as WHY NOT SHARED',
	instead:
		'An exemption is for a surface that IS the control, an element the browser owns, or a test harness. Convert instead where you can.',
	offenders: excused
});
if (excusedFell) complaints.push(excusedFell);

for (const rule of RULES) {
	const worst = offenders[rule.id]
		.sort((a, b) => b.found - a.found)
		.slice(0, 8)
		.map((one) => `${one.found}x  ${one.where}`);
	const complaint = await ratchet('handrolled', rule.id, counts[rule.id], {
		what: rule.what,
		instead:
			`Use the shared component instead: ${rule.instead}\n` +
			`    If a shared component genuinely cannot express what is needed, EXTEND IT,\n` +
			`    in one place, for every caller, rather than working around it here.\n` +
			`    If the SITE element is genuinely the answer (a surface that is itself the\n` +
			`    control, an element whose behaviour the browser owns, a test-only harness), say\n` +
			`    so in the file, on one line, in this exact spelling, naming this rule:\n` +
			`      ${SITE_IS_THE_ANSWER} ${rule.id}: <the reason>`,
		offenders: worst
	});
	if (complaint) complaints.push(complaint);
}

if (complaints.length > 0) {
	console.error('\nHand-rolled copies of shared components.\n');
	for (const complaint of complaints) console.error(`  ${complaint}\n`);
	process.exit(1);
}

console.log(
	`hand-rolled: ${RULES.map((rule) => `${rule.id} ${counts[rule.id]}`).join(', ')} ` +
		`(at the recorded numbers); ${excused.length} (file, rule) exemption(s), recorded`
);
