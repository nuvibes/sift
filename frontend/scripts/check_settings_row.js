// A settings pane is a column of ROWS, and one component decides what a row is.
//
// ## What this counts
//
// `<Field` inside `src/lib/settings-ui/`. `Field` stacks its label above a full-width control,
// which is right for a form and wrong for a settings pane: a pane pins every control to one
// right-hand column, and a stacked full-width box dropped between two real rows reads as a row that
// has broken. Mixed freely, a pane's controls end at many different right edges and agree about
// nothing.
//
// ## What is NOT counted
//
// A `<Field` INSIDE a `<FormCard> ... </FormCard>` block. A short FORM standing on a pane (add a
// login, add a guest, choose a PIN) genuinely is a stack of labelled boxes, and `FormCard` is how
// a file says that is what this is. It is not a row.
//
// So the rule is: a `Field` on a PANE is the fault, a `Field` in a FORM is the form's. The count
// may fall and may never rise, with no case at all in which it is allowed to go up: a number that
// rose when something correct was added could not be read as a debt.
//
// A new settings row uses `SettingRow` (from the registry) or `LabelledRow` (for a value the pane
// holds itself). A new form uses `FormCard` with `Field`s inside it, and this number does not move.
//
// ## The shell is not a pane either
//
// `settings-ui/` holds the panes AND the chrome around them: the shell, the list, the search box.
// The rule above is about a control in a column of rows; the shell's own furniture is not in that
// column and has no right-hand edge to agree with. `NOT_A_PANE` is empty today and is kept, along
// with the check that every entry must be excusing something, for the next piece of shell.

import { readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

import { everySvelteFile, ratchet, recorded, SOURCE } from './lib/tree.js';

const PANES = join(SOURCE, 'lib', 'settings-ui');

/** How the stacked form field is written. Both spellings, because either compiles. */
const FIELD = /<Field[\s/>]/g;

/**
 * A `<FormCard ...>` and everything up to its close: a FORM, which is not a column of rows.
 *
 * Non-greedy, so two forms on one pane are two blocks rather than one block swallowing every row
 * between them. `FormCard` does not nest (a form inside a form is not a thing anybody draws),
 * so there is nothing here a nesting-aware scan would get right that this gets wrong.
 */
const FORM = /<FormCard[\s/>][\s\S]*?<\/FormCard>/g;

/** The source with every `FormCard` block blanked, newlines kept so a line still points at itself. */
function outsideAForm(source) {
	return source.replace(FORM, (found) => found.replace(/[^\n]/g, ' '));
}

/**
 * How many files must be read before an answer is believed.
 *
 * A walk that finds nothing looks exactly like a tree with nothing wrong in it, and this one
 * points at a hard-coded directory, so a rename upstream would empty it silently and this gate
 * would pass for ever afterwards. Sift's settings folder holds forty-odd components.
 */
const AT_LEAST = 20;

/*
 * The pattern, proved against strings whose answer is known.
 *
 * `AT_LEAST` guards the WALK: it catches a renamed directory. It cannot catch a broken PATTERN:
 * a regex that matches nothing reports zero, zero is below any baseline, and the gate passes for
 * ever while every pane fills up with stacked fields. So the pattern is asked three questions it
 * must get right before it is used on anything.
 */
for (const yes of ['<Field label="x">', '<Field/>', '<Field\n\tlabel="x"']) {
	FIELD.lastIndex = 0;
	if (!FIELD.test(yes)) {
		console.error(`The pattern failed to match ${JSON.stringify(yes)}, which it must.`);
		process.exit(1);
	}
}
FIELD.lastIndex = 0;
if (FIELD.test('<FieldSet>')) {
	console.error('The pattern matched <FieldSet>, which is a different component.');
	process.exit(1);
}
FIELD.lastIndex = 0;

/*
 * And the form blanker, asked the same kind of questions before it is used on anything.
 *
 * A blanker matching NOTHING leaves every form field in the count, which fails loudly and is the
 * safe direction. A blanker matching too MUCH is the dangerous one: it would blank a whole pane and
 * report zero for ever. So both edges are pinned: a field inside a form is not counted, a field
 * after the form's close still is, and two forms do not swallow the rows lying between them.
 */
for (const [source, left] of [
	['<FormCard><Field label="x" /></FormCard>', 0],
	['<FormCard><Field /></FormCard>\n<Field />', 1],
	['<FormCard><Field /></FormCard>\n<Field />\n<FormCard><Field /></FormCard>', 1],
	['<Field />', 1]
]) {
	const got = outsideAForm(source).match(FIELD)?.length ?? 0;
	FIELD.lastIndex = 0;
	if (got !== left) {
		console.error(
			`Blanking forms out of ${JSON.stringify(source)} left ${got} field(s), and it must leave ${left}.`
		);
		process.exit(1);
	}
}

/**
 * Components under `settings-ui/` that are the SHELL rather than a pane, with the reason.
 *
 * A named list rather than a rule, because each is a judgement, and every entry has to keep
 * earning its place, which the check below enforces: an entry that names a file with no `<Field` in
 * it changes no outcome, so it cannot be told from one that does, and it would vouch silently the
 * day that file grew one.
 */
const NOT_A_PANE = {};

const found = [];
let read = 0;
/** Which excuses were seen doing work: the file exists and draws at least one stacked field. */
const excusing = new Set();

for (const path of await everySvelteFile(PANES)) {
	read += 1;
	const source = await readFile(path, 'utf8');
	// The fields a FORM holds are the form's. See the head of this file for why they are not
	// counted.
	const uses = outsideAForm(source).match(FIELD)?.length ?? 0;
	FIELD.lastIndex = 0;
	const where = relative(PANES, path).split('\\').join('/');
	if (where in NOT_A_PANE) {
		if (uses > 0) excusing.add(where);
		continue;
	}
	if (uses > 0) found.push({ where, uses });
}

/*
 * Every excuse must be excusing something, the same property this gate asks of the panes.
 *
 * An entry naming a file that has gone, or one that draws no stacked field, changes no outcome,
 * so it cannot be told from an entry that does, and it goes on vouching silently the day that file
 * grows one.
 */
const inert = Object.keys(NOT_A_PANE).filter((name) => !excusing.has(name));
if (inert.length > 0) {
	console.error(
		'These are named in NOT_A_PANE and are excusing nothing: the file has gone, or it draws\n' +
			'no stacked field at all:\n  ' +
			inert.join('\n  ') +
			'\n\nRemove them. An entry that changes no outcome cannot be told from one that does.'
	);
	process.exit(1);
}

if (read < AT_LEAST) {
	console.error(
		`Only ${read} components were read under settings-ui, and there should be at least ${AT_LEAST}.\n` +
			'The walk found nothing to check, which is not the same as nothing being wrong.'
	);
	process.exit(1);
}

const total = found.reduce((sum, one) => sum + one.uses, 0);

const complaint = await ratchet('settings-row', 'fields', total, {
	what: 'stacked form field(s) on a settings pane, outside any FormCard',
	instead:
		'A settings ROW is `SettingRow` (from the registry) or `LabelledRow` (for a value the\n' +
		'    pane holds itself). `Field` is for a FORM, and a form on a pane goes inside a\n' +
		'    `FormCard`, which is not counted at all, so a real form costs this number nothing.\n' +
		'    The recorded number may never be raised.',
	offenders: found.map((one) => `${one.where}: ${one.uses}`)
});

if (complaint) {
	console.error(complaint);
	process.exit(1);
}

console.log(
	`settings rows: ${total} stacked form field(s) on a pane, outside a FormCard ` +
		`(baseline ${(await recorded('settings-row')).fields})`
);
