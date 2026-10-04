#!/usr/bin/env node
/*
 * A TOOLTIP ON AN ICON-ONLY CONTROL IS ITS VISIBLE LABEL, AND THE ACCESSIBLE NAME MUST CONTAIN IT.
 *
 * ## Why
 *
 * A control that announces itself to a screen reader by one name and shows a different sentence to
 * everybody else ("Filter" against "Narrow what is on screen") leaves two people looking at the
 * same button unable to tell each other which one they mean. It is WCAG 2.5.3 Label in Name: the
 * accessible name is allowed to say MORE than the visible label, and it is not allowed to say
 * something else instead. Speech control is the sharpest case: somebody says "click Filter" and
 * nothing happens, because the button is called something no part of the screen shows them.
 *
 * ## What this checks, and what it deliberately cannot
 *
 * Only the case where BOTH are plain strings written into the markup. A tooltip built from a
 * variable is not readable here, and guessing at one would either pass everything or produce
 * failures nobody can act on. Those are left to review: the dynamic ones in Sift are the "one of
 * many identical rows" shape, where the accessible name adds which row it is ("Hear only this"
 * against "Hear only cell 3"), and that is the standard being honoured rather than broken.
 *
 * ## Why the containment test is one-directional
 *
 * The accessible name may add a disambiguator and must not replace the words. "Download" inside
 * "Download the original file" is fine in one direction and a failure in the other: what is on
 * screen has to be sayable.
 *
 * ## The second rule: nothing in a tooltip may be operated
 *
 * `Tooltip` takes a `detail` snippet, so a kept filter can show what it holds as chips before it is
 * pressed: a row of chips is not something a string can say. A tooltip is portalled, is not
 * focusable, and vanishes the moment the pointer leaves the thing it belongs to, so a button drawn
 * in one cannot be reached by a keyboard at all, and by a mouse only by crossing a gap that
 * dismisses it. It would look completely correct in the markup, in review, and in a screenshot.
 *
 * So: chips, marks, a swatch. Never a button, a link, a field, or anything carrying a handler.
 */
import { readdir, readFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, '..', 'src');

async function* walk(dir) {
	for (const entry of await readdir(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) yield* walk(full);
		else if (entry.name.endsWith('.svelte')) yield full;
	}
}

/* `<Tooltip ... label="...">`, capturing only a plain string. A `label={...}` is skipped. See
   the header for why a dynamic one cannot be judged from here. */
const TOOLTIP = /<Tooltip\b[^>]*?\blabel="([^"]*)"/g;
/* Every tooltip, label or no label. What the second rule reads. */
const ANY_TOOLTIP = /<Tooltip\b[^>]*?>/g;
/* The first accessible name inside the tooltip's children. */
const ARIA = /\baria-label="([^"]*)"/;
/** How far past the opening tag to look for the control. A tooltip wraps ONE thing. */
const CLOSES = '</Tooltip>';

/*
 * --- The second rule: nothing operable inside a tooltip's `detail`. ---
 */

/** A raw element somebody can press, follow, type in or choose from. */
const OPERABLE_ELEMENT =
	/<(button|a|input|select|textarea|label|Button|Pressable|Switch|Select|NumberInput|ChooseFile|RowMenu|ContextMenu|DropdownMenu)\b/;
/*
 * Any event handler at all, on anything.
 *
 * Broader than the element list on purpose, and it is the half that catches what the list cannot: a
 * component this gate has never heard of, given an `onclick`, is a control. Written to require an
 * `=` so a PROP called `once` or a word like `only` in prose cannot match.
 */
const HANDLER = /\bon[a-z]+\s*=/;
/*
 * A `tabindex` that puts something in the tab order.
 *
 * `tabindex="-1"` is not operable (it is how a container is made focusable for a script), so only
 * a non-negative one counts.
 */
const REACHABLE = /\btabindex\s*=\s*"?\{?\s*0/;

/** Whether a fragment of markup contains something a person could operate. */
function operable(markup) {
	if (OPERABLE_ELEMENT.test(markup)) return 'an element somebody can operate';
	if (HANDLER.test(markup)) return 'an event handler';
	if (REACHABLE.test(markup)) return 'a tabindex that puts it in the tab order';
	return null;
}

/*
 * A KNOWN POSITIVE FOR THE DETECTOR ITSELF, run before anything is read off the disk.
 *
 * The file-count floor below cannot see this hole. Broken to match nothing, `operable` reports
 * every detail snippet as clean, the count of snippets examined stays exactly what it was, and the
 * gate passes for ever having judged nothing. So the patterns are put to one of each thing they
 * claim to know, and to markup that is genuinely only description.
 */
for (const control of [
	'<button type="button">Press</button>',
	'<a href="/browse">Go</a>',
	'<input type="text" />',
	'<Button onclick={go}>Go</Button>',
	'<Pressable class="name">Named</Pressable>',
	'<FilterChip onremove={() => drop(part)} values={values} />',
	'<div tabindex="0">Focusable</div>'
]) {
	if (!operable(control)) {
		console.error(`The operable-markup detector no longer fires on: ${control}`);
		process.exit(1);
	}
}
for (const description of [
	'<FilterChip field={part.field} values={part.values} all={part.all} />',
	'<span class="swatch" style="background: {colour}"></span>',
	'<p>Only this account can see it.</p>'
]) {
	if (operable(description)) {
		console.error(`The operable-markup detector fires on plain description: ${description}`);
		process.exit(1);
	}
}

/**
 * The body of `{#snippet <name>(...)}` ... `{/snippet}`, counting depth so a nested snippet cannot
 * end the outer one early.
 */
function snippetBody(text, name) {
	const opens = new RegExp(`\\{#snippet\\s+${name}\\s*\\(`, 'g');
	const found = opens.exec(text);
	if (!found) return null;
	let at = found.index + found[0].length;
	let depth = 1;
	while (depth > 0) {
		const nextOpen = text.indexOf('{#snippet', at);
		const nextClose = text.indexOf('{/snippet}', at);
		if (nextClose === -1) return text.slice(found.index);
		if (nextOpen !== -1 && nextOpen < nextClose) {
			depth += 1;
			at = nextOpen + '{#snippet'.length;
			continue;
		}
		depth -= 1;
		if (depth === 0) return text.slice(found.index, nextClose);
		at = nextClose + '{/snippet}'.length;
	}
	return null;
}

const complaints = [];

/*
 * How many pairs this must find before "no complaints" means anything.
 *
 * A GATE CAN MEASURE AN EMPTY SCREEN. Point the walk at a suffix no file has and this reads
 * nothing, compares nothing, and prints the same reassuring line it prints when the
 * whole tree is clean. Every gate that reports an absence needs a known positive, and the cheapest
 * honest one is the count of things it actually looked at.
 *
 * Deliberately far below the real figure so it is not a second baseline to keep in step: it
 * catches a scan that broke, not a tooltip somebody removed.
 */
const AT_LEAST = 10;
let compared = 0;

/** Tooltips carrying a `detail` snippet this gate was able to read, and what was wrong with them. */
const operated = [];
let details = 0;

for await (const full of walk(SOURCE)) {
	const where = relative(SOURCE, full).split('\\').join('/');
	const text = await readFile(full, 'utf8');
	/*
	 * Every tooltip in the file, whether or not its label is a plain string: the operable rule
	 * does not care what the words are, and matching only the quoted form would let a tooltip with
	 * a dynamic label carry a button.
	 */
	for (const found of text.matchAll(ANY_TOOLTIP)) {
		const after = text.slice(found.index + found[0].length);
		const end = after.indexOf(CLOSES);
		const inside = end === -1 ? after : after.slice(0, end);
		/* Declared inside the tooltip, which is how Svelte passes a snippet as a prop, or declared
		   elsewhere in the file and named. Both are followed; the second is the evasion the first
		   check on its own would miss. */
		const handed = /\bdetail=\{([A-Za-z_$][\w$]*)\}/.exec(found[0]);
		const body = handed ? snippetBody(text, handed[1]) : snippetBody(inside, 'detail');
		if (body !== null) {
			details += 1;
			const wrong = operable(body);
			if (wrong) {
				const line = text.slice(0, found.index).split('\n').length;
				operated.push(`src/${where}:${line}: the detail snippet contains ${wrong}`);
			}
		}
	}

	for (const found of text.matchAll(TOOLTIP)) {
		const after = text.slice(found.index + found[0].length);
		const end = after.indexOf(CLOSES);
		const inside = end === -1 ? after : after.slice(0, end);
		const named = ARIA.exec(inside);
		if (!named) continue;
		const shown = found[1].trim();
		const announced = named[1].trim();
		if (!shown || !announced) continue;
		/* A quoted attribute can still be dynamic: Svelte interpolates inside one. `label="{a} - {b}"`
		   is a template, not a string, and comparing its SOURCE against another template's source
		   compares two pieces of code rather than two things a person reads. Same reason
		   `label={...}` is not matched at all. */
		if (shown.includes('{') || announced.includes('{')) continue;
		compared += 1;
		if (announced.toLowerCase().includes(shown.toLowerCase())) continue;
		const line = text.slice(0, found.index).split('\n').length;
		complaints.push(
			`src/${where}:${line}\n` + `    on screen : ${shown}\n` + `    announced : ${announced}`
		);
	}
}

if (complaints.length > 0) {
	console.error(
		'A tooltip is the visible label of the control it wraps, so the accessible name has to\n' +
			'contain it (WCAG 2.5.3). These say two different things, so somebody reading the screen\n' +
			'and somebody hearing it cannot name the same button:\n'
	);
	for (const one of complaints) console.error(one + '\n');
	console.error(
		"Fix by making the tooltip the control's NAME and letting the accessible name add whatever\n" +
			'tells it apart from its neighbours: "Filter", announced as "Filter"; "Hear only this",\n' +
			'announced as "Hear only cell 3".'
	);
	process.exit(1);
}

if (operated.length > 0) {
	console.error(
		'A tooltip is portalled, is not focusable, and vanishes when the pointer leaves the control\n' +
			'it belongs to, so anything operable drawn inside one is a control nobody can reach with a\n' +
			'keyboard and most people cannot reach with a mouse. These carry one:\n'
	);
	for (const one of operated) console.error('  ' + one);
	console.error(
		'\nA `detail` snippet is DESCRIPTION ONLY: chips, marks, a swatch. If somebody has to be able\n' +
			'to press it, it belongs on the control the tooltip is attached to, or in a popover.'
	);
	process.exit(1);
}

/*
 * A FLOOR FOR THE SECOND RULE TOO, and it is a different hole from the one below.
 *
 * `compared` counts label/name pairs and would stay healthy while `snippetBody` returned null for
 * everything. So the detail rule could stop reading anything at all and this file would go on
 * printing a clean line about the first rule. There is exactly one detail snippet in the tree, and
 * one is the honest floor: the day somebody deletes it this goes red, and taking the floor out is
 * then a deliberate act rather than a silence.
 */
if (details < 1) {
	console.error(
		'No tooltip `detail` snippet was found anywhere, so the operable rule judged nothing.\n' +
			'Either the snippet scan has stopped matching what it is aimed at, or the last `detail`\n' +
			'in the tree has gone, in which case delete this floor deliberately.'
	);
	process.exit(1);
}

if (compared < AT_LEAST) {
	console.error(
		`Only ${compared} tooltip/name pairs were found, and there should be at least ${AT_LEAST}.\n` +
			'Nothing was compared, so a clean report here would mean nothing. The walk or one of the\n' +
			'two patterns has stopped matching what it is aimed at.'
	);
	process.exit(1);
}

console.log(
	`every plain tooltip is contained in the name it announces (${compared} checked), and no\n` +
		`tooltip detail can be operated (${details} checked)`
);
