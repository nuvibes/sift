// A field on a settings pane is a settings row, never a bare `Field`.
//
// ## The failure this is for
//
// A form on a pane (a new password, a PIN, a guest's name, a stash-box's key) built from the bare
// `Field` stacks its label over its box and puts the save press under the box on a line of its
// own, and it draws no "Copy settings path" beside its name, which every other row on the pane has.
// Each one looks reasonable alone.
//
// ## The rule
//
// A pane under `lib/settings-ui/` draws a field through `FieldRow`, the field form of the settings
// row: the name and its help on the left, the box and its press on the right, the path copy and
// the line between two rows from `LabelledRow`. This counts every `<Field` tag left in a pane's
// markup (comments aside; tests and probes aside) and holds the count where it is recorded: it
// may only fall, and it is recorded at zero.
//
// Run: node scripts/check_settings_fields.js

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { bareFieldsIn } from './lib/settings-fields.js';
import { everySvelteFile, fromSource, ratchet, SOURCE } from './lib/tree.js';

const PANES = join(SOURCE, 'lib', 'settings-ui');

/** How many files must be read before an answer is believed: a moved folder reads as zero. */
const AT_LEAST = 20;

/** A test harness or probe draws whatever it is testing, on purpose. */
const NOT_A_PANE = /\.test\.svelte$|Probe/;

const files = (await everySvelteFile(PANES)).filter((path) => !NOT_A_PANE.test(path));
if (files.length < AT_LEAST) {
	console.error(
		`\n  settings-fields: read only ${files.length} panes under ${fromSource(PANES)}.\n`
	);
	process.exit(1);
}

let count = 0;
const offenders = [];

for (const path of files) {
	const found = bareFieldsIn(await readFile(path, 'utf8'));
	if (found === 0) continue;
	count += found;
	offenders.push(`${fromSource(path)}  ${found}`);
}

const complaint = await ratchet('settings-fields', 'bare', count, {
	what: 'bare Field(s) on a settings pane',
	instead:
		'Instead: draw it as `FieldRow` (lib/settings-ui/FieldRow.svelte), the same snippet\n' +
		"    for the box and the form's press beside the box of the field it saves.",
	offenders
});

if (complaint) {
	console.error(`\n  ${complaint}\n`);
	process.exit(1);
}

console.log(`Settings fields: ${count} bare field(s) on ${files.length} panes.`);
