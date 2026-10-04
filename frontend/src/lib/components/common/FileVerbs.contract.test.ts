/*
 * One place declares what can be done to a file, one place writes it, and one place calls it.
 *
 * `FileVerbs` exists so that every surface showing files shares one set of actions. A grid wiring
 * its own copy of the sheets and handlers, or a screen calling rename, move, hide and share
 * directly, would make "hide a file" exist several times, and a fix or a new action would have to
 * be made in each place with nothing noticing when they drifted.
 *
 * Three rules, checked here, each closing a different half of that:
 *
 *   1. The verb LIST is built in one file. A surface that wants a verb asks for it.
 *   2. Each endpoint that CHANGES a file has a named caller. More than one is allowed where the
 *      callers are genuinely different questions, and each one has to say which.
 *   3. A shared component has at least two users, or it is premature or unadopted.
 *
 * The third is the one worth the most, because it catches the habit rather than an instance: a
 * component one screen uses is a component the others have not adopted.
 *
 * Static. It reads the source, so what it proves is that there is one caller, not that the caller
 * is right.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file rather than from the working directory: the runner
 *  is started from more than one place and a relative root silently surveys nothing. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

const HOST = 'lib/components/common/FileVerbs.svelte';

/**
 * Every endpoint that changes a file, and who is allowed to ask for it.
 *
 * A list rather than a count, and each entry says why it is there. Hiding has two callers because
 * they are two different questions: "hide what is picked" and "take this one thing back out of
 * the vault, from the panel that explains what is hiding it", and a merge that made them one
 * would make the code worse to satisfy a rule.
 */
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

/**
 * What a file takes off the shared index, by name.
 *
 * The import clause and not the whole source: a component named in a comment is not a user of it.
 * An import is the only mention that is a use.
 */
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
	// The generated schema names every endpoint by definition, and a test naming one is describing
	// a caller rather than being one.
	.filter((file) => file.where !== 'lib/api/schema.d.ts' && !file.where.endsWith('.test.ts'));

describe('the file verbs are declared and wired in one place', () => {
	it('surveys the interface rather than an empty tree', () => {
		// Without this every rule below passes by finding nothing, which is what a wrong root looks
		// like from the outside.
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
		// The list is only worth having if adding a name to it costs an explanation. A blank reason
		// is how an allowlist turns into a record of what happened to be true.
		for (const write of WRITES) {
			for (const [caller, reason] of Object.entries(write.callers)) {
				expect(reason.length, `${write.endpoint} -> ${caller}`).toBeGreaterThan(20);
			}
		}
	});
});

/**
 * The shared components that legitimately have one user, and why each is allowed to.
 *
 * A reason per entry, for the same purpose the endpoint list above has one: an exception with no
 * explanation is how an allowlist becomes a record of whatever happened to be true. None of these
 * is "it only has one caller": that is the thing being excused, not the excuse.
 */
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
	/*
	 * `TagChip.svelte`, `PickMenu.svelte` and `RowMenu.svelte` have more than one user, so the
	 * ordinary rule covers them. Every pick on a menu of verbs still comes through `VerbMenuItems`;
	 * the same list drawn as the whole content of a header button's menu is the picker being the
	 * app's one picker, not a verb escaping the declaration. `RowMenu`'s requirement (both gestures
	 * off one declared list) is stated in its own head.
	 */
};

describe('a shared component has more than one user', () => {
	/*
	 * The cheapest check in the interface and the one that catches the habit.
	 *
	 * Something put in `common/` is a claim that more than one screen wants it. One user means the
	 * claim has not been made good: either it was extracted too early, or it was extracted for a
	 * reason nobody finished acting on.
	 */
	const shared = files.filter(
		(file) =>
			file.where.startsWith('lib/components/common/') &&
			file.where.endsWith('.svelte') &&
			/* A `.test.svelte` is a harness, not a component. It exists so a test can mount something
			   that takes snippets (which cannot be written in a `.ts` file) and it has exactly one
			   user by definition: the test it was written for. Counting one would fail this check on
			   the day such a harness is written, for a file that is not a claim about anything.
			   `check_bits_first.js` already skips them for the same reason. */
			!file.where.endsWith('.test.svelte')
	);

	it('has components to check', () => {
		expect(shared.length).toBeGreaterThan(10);
	});

	it('leaves out harnesses and nothing else', () => {
		/* NARROWING A CHECK IS MORE DANGEROUS THAN ADDING ONE, because it cannot fail: take too much
		   out and the list simply gets shorter and everything above goes on passing. The reasoning
		   for the line above is that a `.test.svelte` has exactly one user by definition, so this
		   holds it to that, in both directions. Every file it removes has to be one, and there has
		   to BE one, or the rule is passing by having nothing to do. */
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
		// An excuse for a component that has been deleted or renamed is an excuse that will be
		// inherited by whatever takes the name next.
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
					// Imported by path, or taken off the shared index by name. The index itself does
					// not count: re-exporting something is not using it.
					if (file.where === 'lib/components/common/index.ts') return false;
					// Nor does the gallery, and this one is load-bearing. The gallery draws every
					// component on purpose, so counting it would add one user to every entry, and
					// this check would pass for everything in `common/` and catch nothing.
					//
					// A prefix, not one file: everything under `routes/design/` exists to draw
					// components, including any prototype added there.
					if (file.where.startsWith('routes/design/')) return false;
					// Nor a test harness: a `.test.svelte` mounts a component so a test can drive
					// it, which is a test using it, not the app.
					if (file.where.endsWith('.test.svelte')) return false;
					/* A WORD BOUNDARY ON THE PATH TOO, and not a substring. `includes('Tabs.svelte')`
					   is true of every file importing `MobileTabs.svelte`, so a component whose name
					   ENDS another one's reads as having users it has not got, and the rule then
					   cannot fail for it, ever. The boundary is what separates `Tabs.svelte` from
					   `MobileTabs.svelte`; the import path in front of a real use always puts a slash
					   or a quote there. */
					return (
						new RegExp(`\\b${stem}\\.svelte`).test(file.source) ||
						barrelNames(file.source).has(stem)
					);
				})
				.map((file) => file.where);

			const excused = ONE_USER_IS_RIGHT[name];
			if (excused) {
				// Excused, but not unchecked: something with a second user has outgrown its excuse and
				// the entry should go, and something with NO user is not excused by anything.
				expect(users.length, `${stem}: ${excused}`).toBe(1);
				return;
			}

			expect(users.length, `${stem} is used by ${users.join(', ') || 'nothing'}`).toBeGreaterThan(
				1
			);
		});
	}
});

/*
 * And the surface a verb is actually pressed on: the bar a wall raises over picked FILES.
 *
 * The rules above keep the LIST in one place. This one keeps the BAR there: a wall could take every
 * verb from the host and still write its own row of buttons (one hand-written `Button` reading
 * "Remove from this collection" beside a grid offering ten verbs over the same files), so the
 * declaration and what somebody presses would be two things again, and nothing anywhere would say so.
 *
 * Keyed on `noun="file"`, which is how a bar says the things it is counting are files. A wall that
 * grows one and does not draw `VerbButtons` fails here rather than quietly offering a different set.
 */
describe('a bar raised over picked files draws the declared verbs', () => {
	const walls = files
		// Not the gallery. It draws the bar as a SPECIMEN, over invented rows, so there is no wall
		// behind it and nothing for the host to own: the same reason the one-user rule above skips
		// that folder.
		.filter((file) => !file.where.startsWith('routes/design/'))
		// A `.test.svelte` is a harness that mounts a bar so a test can drive it; it is not a wall.
		.filter((file) => !file.where.endsWith('.test.svelte'))
		// The BAR's own noun, not any `noun="file"` on the page: a wall's page search
		// (`WallControls`) names its noun the same way and raises nothing over picked files.
		.filter((file) => /<ActionBar\b[^>]*\bnoun="file"/s.test(file.source))
		.map((file) => file.where)
		.sort();

	it('finds the walls, so the rule below is not vacuous', () => {
		// Named, because a rule that surveys whatever it happens to find proves nothing on the day a
		// rename makes it find none of them.
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
			// The tag and not the prefix: `<VerbButtonsOfMyOwn` contains `<VerbButtons`, so a plain
			// substring would pass on the one thing this is looking for: a wall that wrote its own.
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
