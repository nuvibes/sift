// Every name a settings pane draws is something the settings search can find.
//
// ## The failure this is for
//
// A row drawn on a pane from the pane's own words and never declared to the search is a row the
// search cannot find: it reads only the registry and each pane's `SEARCHABLE`. Nothing goes red:
// the row is on the screen, and the search simply cannot see it. The file-level gate in `tests/gates/` asks whether a pane with a
// control has a `.search.ts` at all; a pane with six rows and one entry passes it.
//
// ## The rule
//
// Every row and heading a pane under `lib/settings-ui/` draws by name (`SettingGroup` headings,
// `SectionHeading`, and the `label` of `LabelledRow`, `ActionRow`, `FieldRow` and `FactRow`) is the
// `name` of an entry in some pane's `SEARCHABLE`, compared without case. The words are READ, not
// matched as text: `label={COPY.pack.create}` is resolved to the string the pane's `.search.ts`
// holds (see `lib/settings-search-covers.js`), so a row and its entry built from the same constant
// agree, and a row whose words changed without its entry goes red.
//
// Registry rows (`SettingRow`, `SettingsList`) are not read here: the registry feeds the index
// itself. A name decided while the screen runs (a tunnel's name, a task's title, a sentence picked
// by a condition) is the person's data or a state, never a setting, and is skipped.
//
// ## The excuse list
//
// A name that is not a setting somebody would look for (a status line's heading, a row inside a
// dialog) is named below with the reason, per file. An excuse that excuses nothing is refused, so
// the list cannot outlive what it excuses.
//
// Run: node scripts/check_settings_search_covers_panes.js

import { readFile, readdir } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { basename, join } from 'node:path';

import { declaredNames, loadSearchModule, said, unfoundIn } from './lib/settings-search-covers.js';
import { everySvelteFile, fromSource, SOURCE } from './lib/tree.js';

const PANES = join(SOURCE, 'lib', 'settings-ui');

/** How many files must be read before an answer is believed: a moved folder reads as zero. */
const AT_LEAST = 20;

/** A test harness or probe draws whatever it is testing, on purpose. */
const NOT_A_PANE = /\.test\.svelte$|Probe/;

/**
 * Names drawn on a pane that are not settings anybody searches for, per file, with the reason.
 * Keyed `File.svelte` then the words as drawn.
 *
 * @type {Record<string, Record<string, string>>}
 */
const EXCUSED = {
	'ApplicationLog.svelte': { Show: "the log view's level filter, not a setting" },
	'GraphicsCard.svelte': {
		'Restart needed': 'a state the pane reports, not something to set',
		"Sift didn't restart": 'a state the pane reports, not something to set',
		'What the GPU reported': 'the answer to Test the GPU, found by that row',
		'Last download': 'the answer to Download the GPU runtime, found by that row'
	},
	'Ledger.svelte': {
		Type: 'a filter on the history shown, not a setting',
		Action: 'a filter on the history shown, not a setting'
	},
	'NetworkSharing.svelte': { Address: 'a fact of Network sharing, found by that heading' },
	'Performance.svelte': { 'Your drives and shares': 'a result of Benchmark this device' },
	'Profile.svelte': {
		'Current password': 'a box of the Password form, found by Password',
		'New password': 'a box of the Password form, found by Password',
		'New password again': 'a box of the Password form, found by Password',
		'Your password': 'a box of the PIN form, found by PIN',
		'New PIN': 'a box of the PIN form, found by PIN'
	},
	'StashBoxes.svelte': {
		'Connect through': 'a box of the stash-box form, found by Stash-boxes',
		'New key': 'a box of the stash-box form, found by Stash-boxes',
		Name: 'a box of the stash-box form, found by Stash-boxes',
		Address: 'a box of the stash-box form, found by Stash-boxes',
		Key: 'a box of the stash-box form, found by Stash-boxes'
	},
	/* Every control on it is a registered setting drawn as a swatch, already in the index from the
	   registry; a declaration beside it would list each twice. */
	'ThemeChoices.svelte': {
		Background: 'a registered setting, drawn as a swatch',
		Accent: 'a registered setting, drawn as a swatch',
		Font: 'the heading over two registered typeface settings',
		Main: 'a registered setting, drawn as a swatch',
		Secondary: 'a registered setting, drawn as a swatch'
	},
	'Tunnels.svelte': {
		Name: 'a box of the Import a tunnel form, found by Tunnels',
		Configuration: 'a box of the Import a tunnel form, found by Tunnels'
	},
	'UnlockField.svelte': { 'Your password': 'the box of the unlock row, found by that row' },
	'Users.svelte': {
		'New guest': 'the form Add a guest opens, found by Guests',
		'First password': 'a box of the new guest form, found by Guests',
		'First password again': 'a box of the new guest form, found by Guests',
		'New password': 'a box of the reset password form for a guest',
		'New password again': 'a box of the reset password form for a guest'
	}
};

const loaded = new Map();
const load = (/** @type {string} */ module) =>
	loadSearchModule(module, (name) => readFileSync(join(PANES, `${name}.ts`), 'utf8'), loaded);

const modules = (await readdir(PANES))
	.filter((name) => name.endsWith('.search.ts'))
	.map((name) => name.replace(/\.ts$/, ''));
const declared = declaredNames(modules, load);

const files = (await everySvelteFile(PANES)).filter((path) => !NOT_A_PANE.test(path));
if (files.length < AT_LEAST || declared.size < 20) {
	console.error(
		`\n  settings-search: read ${files.length} panes and ${declared.size} declared names under ` +
			`${fromSource(PANES)}. That is too few to be right.\n`
	);
	process.exit(1);
}

const offenders = [];
for (const path of files) {
	const file = basename(path);
	const excused = new Set(Object.keys(EXCUSED[file] ?? {}).map(said));
	const source = await readFile(path, 'utf8');
	for (const one of unfoundIn(source, declared, load, excused)) {
		offenders.push(`${fromSource(path)}:${one.line}  "${one.words}"`);
	}
}

/* An excuse must still name something the file draws, or it vouches for nothing. */
const stale = [];
for (const [file, names] of Object.entries(EXCUSED)) {
	const path = join(PANES, file);
	let source = '';
	try {
		source = await readFile(path, 'utf8');
	} catch {
		stale.push(`${file} (no such pane)`);
		continue;
	}
	const drawn = new Set(unfoundIn(source, new Set(), load).map((one) => said(one.words)));
	for (const words of Object.keys(names)) {
		if (!drawn.has(said(words))) stale.push(`${file}  "${words}"`);
	}
}

if (offenders.length > 0 || stale.length > 0) {
	if (offenders.length > 0) {
		console.error(
			`\n  settings-search: ${offenders.length} name(s) drawn on a settings pane that the search ` +
				`cannot find:\n    ${offenders.join('\n    ')}\n\n` +
				"  Declare each as an entry in that pane's `SEARCHABLE` (its `.search.ts`), named from the\n" +
				'  same constant the pane draws, with the words somebody would type as `keywords`. A name\n' +
				'  that is not a setting goes in EXCUSED in this file, with the reason.\n'
		);
	}
	if (stale.length > 0) {
		console.error(
			`\n  settings-search: ${stale.length} excuse(s) name nothing the pane draws:\n    ` +
				`${stale.join('\n    ')}\n\n  Remove them.\n`
		);
	}
	process.exit(1);
}

console.log(
	`Settings search: every name on ${files.length} panes is one of ${declared.size} declared entries.`
);
