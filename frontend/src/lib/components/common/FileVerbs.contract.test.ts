/* One place declares what can be done to a file, one writes it, one calls it: the verb list is
 * built in one file, each changing endpoint has named callers, and a shared component has two
 * users. Static: it proves one caller, not a right one. */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** All of `src`, resolved from this file, since the runner starts from more than one place. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

const HOST = 'lib/components/common/FileVerbs.svelte';

/** Every endpoint that changes a file and who may ask for it, each with its reason. */
const WRITES: { endpoint: string; pattern: RegExp; callers: Record<string, string> }[] = [
	{
		endpoint: 'rename a file',
		pattern: /`\/assets\/\$\{[^}]+\}\/rename`/,
		callers: {
			'lib/components/FileActions.svelte':
				'the button under a file, which is where somebody who wants to rename one looks first',
			'lib/components/AssetView.svelte':
				'the FILENAME box on the record, which saves through this route rather than through the record write: renaming moves a file on the disk and can be refused for reasons no other field has, so the refusals stay written in one place and both ways in reach them'
		}
	},
	{
		endpoint: 'move a file',
		pattern: /'\/assets\/move'/,
		callers: {
			'lib/grid/actions.svelte.ts': 'the shared actions, behind the move verb, for any surface'
		}
	},
	{
		endpoint: 'undo a move',
		pattern: /`\/moves\/\$\{[^}]+\}\/undo`/,
		callers: {
			'lib/components/FileActions.svelte':
				'only a single file screen knows there was a move to put back',
			'lib/api/history.ts':
				"the Undo on a row of a history, which is a different question from the one above: that one is the move somebody just made from this screen and this one is any move in the record of what has happened to a thing, reached by its own id and possibly made months ago on another screen. Several screens draw a history (a file's record panel, a person's page and a queue's), so `undoHistoryEvent` is the one place that knows which of the two doors a kind of event opens"
		}
	},
	{
		endpoint: 'hide a file',
		pattern: /`\/assets\/\$\{[^}]+\}\/vault`/,
		callers: {
			'lib/grid/actions.svelte.ts': 'the shared actions, behind the hide verb, for any surface',
			'lib/library/sharing.ts':
				'one branch of taking a thing back out of the vault from whatever is hiding it: a file, a folder, a tag, a person; the file branch happens to be this endpoint'
		}
	}
];

/** What a file imports off the shared index: an import is the only mention that is a use. */
function barrelNames(source: string): Set<string> {
	const names = new Set<string>();
	for (const clause of source.matchAll(
		/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'\$lib\/components\/common'/g
	)) {
		for (const one of clause[1].split(',')) {
			const name = one
				.trim()
				.replace(/^type\s+/, '')
				.split(/\s+as\s+/)[0]
				.trim();
			if (name) names.add(name);
		}
	}
	return names;
}

function everySourceFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everySourceFile(path));
		else if (entry.endsWith('.svelte') || entry.endsWith('.ts')) found.push(path);
	}
	return found;
}

const files = everySourceFile(SOURCE)
	.map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}))
	// The schema and tests name endpoints without calling them.
	.filter((file) => file.where !== 'lib/api/schema.d.ts' && !file.where.endsWith('.test.ts'));

describe('the file verbs are declared and wired in one place', () => {
	it('surveys the interface rather than an empty tree', () => {
		// Or every rule passes by finding nothing.
		expect(files.length).toBeGreaterThan(100);
		expect(files.some((file) => file.where === HOST)).toBe(true);
	});

	it('builds the verb list in one file', () => {
		const building = files
			.filter((file) => /\bfileVerbs\s*\(/.test(file.source))
			.map((file) => file.where)
			// The module that defines it, and the host that calls it.
			.filter((where) => where !== 'lib/grid/verbs.ts');

		expect(building).toEqual([HOST]);
	});

	it('does build it there, so the rule above is not vacuous', () => {
		const host = files.find((file) => file.where === HOST);
		expect(/\bfileVerbs\s*\(/.test(host?.source ?? '')).toBe(true);
	});
});

describe('every endpoint that changes a file has a named caller', () => {
	for (const write of WRITES) {
		it(`${write.endpoint}`, () => {
			const calling = files
				.filter((file) => write.pattern.test(file.source))
				.map((file) => file.where)
				.sort();

			expect(calling).toEqual(Object.keys(write.callers).sort());
		});
	}

	it('names a reason against every caller', () => {
		// A blank reason is how an allowlist turns into a record.
		for (const write of WRITES) {
			for (const [caller, reason] of Object.entries(write.callers)) {
				expect(reason.length, `${write.endpoint} -> ${caller}`).toBeGreaterThan(20);
			}
		}
	});
});

/** Shared components that rightly have one user, each with why. */
const ONE_USER_IS_RIGHT: Record<string, string> = {
	'PressSize.svelte':
		"the one holder that draws two kinds of press in one form (a form's answers beside its small rows); every other holder gives one size from its own script, and a second holder with two kinds would be the second user",
	'Meter.svelte':
		'the one bar that draws a share of a whole as a measure (how sure Sift is about a person, under the faces on a person page); a second share drawn anywhere comes through here',
	'EntityRow.svelte':
		'the one row shape the popout player draws five times (people, sites, collections, Photo Sets, tags), so the five rows cannot drift; it is in common because the rule it holds (one line, the kind glyph on the left, See all on the right) is a design rule and the gallery draws it, and a second screen with a band of chips takes it as it is',
	'Toaster.svelte':
		'mounted once for the whole app by the root layout, which is what a single toast host means; a second one would draw every message twice',
	'Tree.svelte':
		'the folder tree, drawn by the library screen; it stays in common rather than moving beside that screen because a tree is the LEFT NAV shape and the explorer on Browse is the main-pane shape: a flat list of one folder is not a tree, and folding the two together would give each of them the other one shortcomings',
	'DateRange.svelte':
		'there is one place in the app where a span of days is chosen (the facet panel) and that panel is drawn by BOTH the filter bar and a theater cell, so the two surfaces share one control rather than one surface having a private one',
	'LinkPreview.svelte':
		"the one door to the library's LinkPreview: the fence refuses bits-ui outside the primitives, so any card that opens on a hovered link has to come through here. One user today (EntityPreview, the card a chip in the file dialog opens); the second kind of thing worth previewing (a collection, a Site) will find it rather than the library",
	'Toggle.svelte':
		"the one door to the library's Toggle: the fence refuses bits-ui outside the primitives, so a pressed run of text anywhere has to come through here. One user today (the exit address in Settings); the second pressed-text control will find it rather than the library",
	'DateField.svelte':
		'reached through RecordForm, which is the one form every kind of record is edited in, so a second direct user would be a screen editing a record without it; it is also one half of a documented pair with DateRange, and the box and segments both draw are a single rule in the stylesheet written for the two of them',
	'ColorPicker.svelte':
		"a square of one hue, a slider through the hues and a hex box, for choosing ONE colour: the custom accent on the appearance pane. One user today because the app has one colour a person chooses (every other colour is a token); a second chosen colour, a tag's or a collection's, would reach for it rather than building a second picker",
	'TimeField.svelte':
		'reached through SettingRow, which is the ONE row that turns a registry setting into a control by the shape of its value, so a second direct user would be a pane deciding for itself what a clock time looks like, which is the drift SettingRow exists to end. The two scheduled hours are the only clock times in the app, and both are drawn through it',
	'HistoryRow.svelte':
		'reached through HistoryList, which is the one list a history is drawn as, so a second direct user would be a screen drawing events with no thread between them and its own idea of which glyph each kind wears. The pair is deliberately two files: the row is one event and the list is the order they are in',
	'ContextMenuSeparator.svelte':
		'drawn by ContextMenuGroup at the head of every group and by nothing else: a line placed by hand is what check_menu_groups.js refuses, so its one user is the rule rather than a gap, and the separator stays its own file so the gallery can show the line itself',
	'PictureViewer.svelte':
		'reached through EntityHeader, which is the header EVERY entity page draws, so a second direct user would be a page opening a picture without the header that owns the one it is showing; it is a flavour of Modal rather than a layout of its own, which is why it sits beside Modal and not beside the header'
	/* TagChip, PickMenu and RowMenu have more than one user, so the ordinary rule covers them. */
};

describe('a shared component has more than one user', () => {
	/* Something in `common/` claims more than one screen wants it. */
	const shared = files.filter(
		(file) =>
			file.where.startsWith('lib/components/common/') &&
			file.where.endsWith('.svelte') &&
			/* A `.test.svelte` is a harness with one user by definition. */
			!file.where.endsWith('.test.svelte')
	);

	it('has components to check', () => {
		expect(shared.length).toBeGreaterThan(10);
	});

	it('leaves out harnesses and nothing else', () => {
		/*
		 * Narrowing a check cannot fail, so this holds the exclusion to exactly harnesses, and
		 * some.
		 */
		const everySvelte = files.filter(
			(file) => file.where.startsWith('lib/components/common/') && file.where.endsWith('.svelte')
		);
		const removed = everySvelte.filter((file) => !shared.includes(file));

		expect(removed.length, 'nothing is being skipped, so this rule proves nothing').toBeGreaterThan(
			0
		);
		for (const one of removed) {
			expect(one.where, 'something that is not a harness was skipped').toMatch(/\.test\.svelte$/);
		}
	});

	it('excuses nothing that is not there', () => {
		// An excuse for a deleted name would pass to whatever takes it next.
		const names = shared.map((component) => component.where.split('/').pop());
		for (const excused of Object.keys(ONE_USER_IS_RIGHT)) {
			expect(names, `${excused} is excused but is not here`).toContain(excused);
		}
	});

	for (const component of shared) {
		const name = component.where.split('/').pop() ?? '';
		it(`${name}`, () => {
			const stem = name.replace('.svelte', '');
			const users = files
				.filter((file) => file.where !== component.where)
				.filter((file) => {
					// By path or by name off the index; the index itself is not a use.
					if (file.where === 'lib/components/common/index.ts') return false;
					// Nor the gallery, which draws everything and would excuse every entry.
					if (file.where.startsWith('routes/design/')) return false;
					// Nor a test harness.
					if (file.where.endsWith('.test.svelte')) return false;
					/* A boundary on the path, or `Tabs.svelte` would match `MobileTabs.svelte`. */
					return (
						new RegExp(`\\b${stem}\\.svelte`).test(file.source) ||
						barrelNames(file.source).has(stem)
					);
				})
				.map((file) => file.where);

			const excused = ONE_USER_IS_RIGHT[name];
			if (excused) {
				// An excused component with a second user, or none, fails.
				expect(users.length, `${stem}: ${excused}`).toBe(1);
				return;
			}

			expect(users.length, `${stem} is used by ${users.join(', ') || 'nothing'}`).toBeGreaterThan(
				1
			);
		});
	}
});

/* The bar a wall raises over picked files (`noun="file"`) draws the declared verbs. */
describe('a bar raised over picked files draws the declared verbs', () => {
	const walls = files
		// Not the gallery, which draws the bar as a specimen.
		.filter((file) => !file.where.startsWith('routes/design/'))
		// A `.test.svelte` is a harness that mounts a bar so a test can drive it; it is not a wall.
		.filter((file) => !file.where.endsWith('.test.svelte'))
		// The bar's own noun, not a page search's.
		.filter((file) => /<ActionBar\b[^>]*\bnoun="file"/s.test(file.source))
		.map((file) => file.where)
		.sort();

	it('finds the walls, so the rule below is not vacuous', () => {
		// Named, so a rename that finds none fails.
		expect(walls).toEqual(
			expect.arrayContaining([
				'lib/components/AssetGrid.svelte',
				'routes/collections/[id]/+page.svelte'
			])
		);
	});

	for (const where of walls) {
		it(`${where} draws its bar from the verbs`, () => {
			const source = files.find((file) => file.where === where)?.source ?? '';
			// The tag, not a prefix that a lookalike name contains.
			expect(/<VerbButtons[\s/>]/.test(source), 'a bar over files with no VerbButtons in it').toBe(
				true
			);
			expect(
				/<FileVerbs[\s/>]/.test(source),
				'verbs drawn without the host that owns the sheets behind them'
			).toBe(true);
		});
	}
});
