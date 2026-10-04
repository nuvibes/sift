/*
 * The bar and the right-click menu offer the same things, on every screen that has both.
 *
 * `verbs.ts` makes that possible (one declared list, two renderers), and it is checked end to end
 * here, against two different faults with the same symptom:
 *
 * 1. The declaration drifts: a verb is built for one surface and not the other. Every declared list
 *    is rendered both ways and compared id for id.
 * 2. The screen filters one of them: a page hands the bar a shorter list, or writes a button by
 *    hand. That cannot be seen from the declaration, so the second half reads the source of every
 *    screen that raises a bar.
 *
 * The one allowed difference is named: `singleOnly`. A bar addresses a set, so a verb meaningless
 * over forty things (renaming forty files to one name, copying forty links, reporting who can see
 * "them") is not on it. Every missing id must be one of those, and the test says which.
 *
 * The words are deliberately not compared: a menu on one file says "Remove from favorites" where a
 * bar over forty says "Favorites", which is `subject` doing its job. Ids are what a surface can
 * reach.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { entityVerbs } from '$lib/components/entity/verbs';
import { faceVerbs, pileVerbs } from '$lib/components/faces/verbs';
import { fileVerbs, type FileVerbHandlers } from '$lib/grid/verbs';
import {
	barShape,
	flatVerbs,
	menuVerbs,
	type Verb,
	type VerbPick
} from '$lib/components/common/verbs';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** Every id the BAR can reach, doors opened, so a verb cannot be hidden from it inside a group. */
function barIds(verbs: readonly Verb[]): string[] {
	const shape = barShape(verbs);
	return flatVerbs([...shape.named, ...shape.rest]).map((verb) => verb.id);
}

/** Every id the MENU can reach, the same way. */
function menuIds(verbs: readonly Verb[]): string[] {
	return flatVerbs(menuVerbs(verbs)).map((verb) => verb.id);
}

/** What was left off the bar, and the words that say it was on purpose. */
function onlyInTheMenu(verbs: readonly Verb[]): string[] {
	const bar = new Set(barIds(verbs));
	return menuIds(verbs).filter((id) => !bar.has(id));
}

/** Every id declared `singleOnly`, which is the only excuse a missing one may have. */
function meaninglessOverASet(verbs: readonly Verb[]): string[] {
	return flatVerbs(verbs)
		.filter((verb) => verb.singleOnly)
		.map((verb) => verb.id);
}

const noop = () => {};

/** A list that answers nothing: the five places a file can be put are lists, not presses. */
const PICK: VerbPick = {
	kind: 'tag',
	plural: 'tags',
	ask: async () => ({ choices: [], more: 0 }),
	pick: async () => 'landed'
};

function handlersForFiles(): FileVerbHandlers {
	return {
		tag: PICK,
		rename: noop,
		collect: PICK,
		assign: PICK,
		site: PICK,
		photoSet: PICK,
		song: PICK,
		favorite: noop,
		pin: noop,
		rate: noop,
		move: noop,
		compress: noop,
		edit: noop,
		gif: noop,
		share: noop,
		visibility: noop,
		hide: noop,
		save: noop,
		link: noop,
		autoEnrich: noop,
		enrich: noop,
		remove: noop
	};
}

const saving = { label: () => 'Save', icon: () => 'download' as const };

/**
 * The declared lists, one per screen that draws both surfaces.
 *
 * Built at their widest (every handler present, an admin, several files picked) because a verb
 * that is absent from both surfaces is not drift and a narrower case would hide the ones that are.
 */
const PAGES: { page: string; verbs: Verb[] }[] = [
	{
		page: 'a wall of files, over a selection',
		verbs: fileVerbs(
			{
				isAdmin: true,
				canSave: true,
				showingHidden: false,
				count: 12,
				canMove: true,
				canCompress: true,
				handlers: handlersForFiles()
			},
			saving
		)
	},
	{
		page: 'a wall of files, as a guest',
		verbs: fileVerbs(
			{
				isAdmin: false,
				canSave: true,
				showingHidden: false,
				count: 12,
				handlers: handlersForFiles()
			},
			saving
		)
	},
	{
		page: 'a wall of people, tags, collections, Sites or Photo Sets',
		verbs: entityVerbs({
			isAdmin: true,
			handlers: {
				rename: noop,
				tag: PICK,
				favorite: noop,
				pin: noop,
				rate: noop,
				share: noop,
				visibility: noop,
				enrich: noop,
				lookUp: noop,
				hide: noop,
				merge: noop,
				remove: noop
			}
		})
	},
	{
		page: 'a wall of faces',
		verbs: faceVerbs({
			showGroup: noop,
			name: noop,
			agree: noop,
			undo: noop,
			move: noop,
			setAside: noop,
			restore: noop,
			remove: noop
		})
	},
	{
		page: 'a wall of face groups',
		verbs: pileVerbs({ name: noop, setAside: noop, restore: noop, remove: noop })
	}
];

describe('the bar and the menu offer the same verbs', () => {
	for (const { page, verbs } of PAGES) {
		it(`offers the same ids on ${page}`, () => {
			// Everything the menu has and the bar has not, and every one of them named as a verb that
			// means nothing pointed at a set. An empty answer on a page with no such verb is the
			// strongest form of this: the two surfaces are then identical.
			expect(onlyInTheMenu(verbs)).toEqual(meaninglessOverASet(verbs));
		});

		it(`offers the bar nothing the menu has not got, on ${page}`, () => {
			// The other direction, which is the one nothing checked. A verb reachable from the bar
			// and not from the menu is a verb somebody can only find by picking something first.
			const menu = new Set(menuIds(verbs));
			expect(barIds(verbs).filter((id) => !menu.has(id))).toEqual([]);
		});

		it(`draws every bar verb exactly once on ${page}`, () => {
			// Named and behind the door are two halves of one list, never two lists. A verb in both
			// would be drawn twice and could be pressed twice.
			const ids = barIds(verbs);
			expect([...new Set(ids)]).toEqual(ids);
		});
	}
});

/*
 * The split itself, on a declaration written for the purpose.
 *
 * The declared lists above happen to put no `singleOnly` verb under a group, so the rule that
 * prunes such a child from the bar is invisible in all of them. Four verbs are `singleOnly`
 * (renaming, copying a link, trimming one clip, reporting who can see one thing), and the first to
 * move under "Add to" would otherwise be reachable from a bar over forty files. So the rule is
 * asked directly, of a list built here. Nothing in it names anything real.
 */
describe('barShape, asked about cases the application does not have yet', () => {
	const stub = (id: string, extra: Partial<Verb> = {}): Verb => ({
		id,
		label: id,
		icon: 'add',
		run: () => {},
		...extra
	});

	it('keeps a verb that means nothing over a set out of a GROUP as well', () => {
		const shape = barShape([
			stub('put', { children: [stub('one', { singleOnly: true }), stub('many')] })
		]);
		const group = shape.named.find((verb) => verb.id === 'put');
		expect(group?.children?.map((child) => child.id)).toEqual(['many']);
	});

	it('draws no door at all where every row behind it was such a verb', () => {
		// A group emptied by the prune is a button that opens onto nothing, which reads as broken.
		expect(barShape([stub('put', { children: [stub('one', { singleOnly: true })] })])).toEqual({
			named: [],
			rest: []
		});
	});

	it('names a group, the stars and the destructive one without being told', () => {
		const shape = barShape([
			stub('put', { children: [stub('one')] }),
			stub('plain'),
			stub('rate', { stars: true }),
			stub('wanted', { primary: true }),
			stub('remove', { destructive: true })
		]);
		expect(shape.named.map((verb) => verb.id)).toEqual(['put', 'rate', 'wanted', 'remove']);
		expect(shape.rest.map((verb) => verb.id)).toEqual(['plain']);
	});
});

/*
 * AND NO SCREEN FILTERS ONE SURFACE BY HAND.
 *
 * The declaration above can be perfectly in step and a page can still hand the bar a shorter list
 * than the menu, or write a button into the bar that is on no menu anywhere. That is what this half
 * reads for, and it is the half that catches the habit rather than an instance.
 *
 * Static: it proves the two surfaces are fed from one split of one list, not that the list is right.
 */
const BAR = /<ActionBar\b/;
/** A bar that draws no declared verbs at all, and why that is honest rather than a gap. */
const NOT_FROM_A_LIST: Record<string, string> = {
	'lib/components/organize/IdentifiedPanel.svelte':
		'one verb, agreeing to the names proposed for the people picked, and there is no right-click menu on that list for it to drift from: a declared list of one would be a claim to two surfaces that does not exist'
};

function everySvelteFile(from: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(from)) {
		const path = join(from, entry);
		if (statSync(path).isDirectory()) {
			// The component gallery is a separate checkout, and not part of the product.
			if (relative(SOURCE, path).replaceAll('\\', '/') === 'routes/design') continue;
			found.push(...everySvelteFile(path));
		} else if (entry.endsWith('.svelte')) {
			found.push(path);
		}
	}
	return found;
}

describe('every screen that raises a bar feeds it from the shared split', () => {
	const bars = everySvelteFile(SOURCE)
		.map((path) => ({
			at: relative(SOURCE, path).replaceAll('\\', '/'),
			text: readFileSync(path, 'utf8')
		}))
		.filter((file) => BAR.test(file.text));

	it('finds the bars', () => {
		// A survey that matched nothing would pass every rule below it in silence.
		expect(bars.length).toBeGreaterThan(5);
	});

	for (const { at, text } of bars) {
		const excuse = NOT_FROM_A_LIST[at];
		it(`draws its verbs through barShape: ${at}`, () => {
			const buttons = [...text.matchAll(/<VerbButtons[^>]*verbs=\{([^}]*)\}/g)].map(
				(found) => found[1]
			);
			if (excuse !== undefined) {
				expect(buttons, excuse).toEqual([]);
				return;
			}
			// One `VerbButtons`, and what it is handed is the NAMED half of a split, never a list
			// this screen built for the bar alone.
			expect(buttons).toHaveLength(1);
			expect(buttons[0]).toMatch(/\bbarShape\(|\bshape\.named\b/);
		});

		it(`keeps the rest of the verbs reachable: ${at}`, () => {
			const door = [...text.matchAll(/<VerbMore[^>]*verbs=\{([^}]*)\}/g)].map((found) => found[1]);
			if (excuse !== undefined) {
				expect(door, excuse).toEqual([]);
				return;
			}
			// The other half of the same split. Without it, everything `barShape` did not name would
			// be unreachable from the bar, which is the very drift this file exists to refuse,
			// arriving through the fix for it.
			expect(door).toHaveLength(1);
			expect(door[0]).toMatch(/\bbarShape\(|\bshape\.rest\b/);
		});
	}
});

/*
 * ONE ADD TO MENU, WHICHEVER DOOR OPENS IT.
 *
 * Three doors open Add to over files: the selection bar's, a file's own (the popout), and the
 * right-click menu's (a tile, a Theater cell). They are one menu only while all three draw the
 * declared door's rows through `VerbMenuItems`. Handed the rows without their lists, the bar
 * would open a sheet per place where every other door opens the list.
 * `FileVerbs.add-to.svelte.test.ts` holds what the host hands each door; this half reads for the
 * habit: an Add to written by hand, a door drawn off something other than the declaration, and a
 * sheet for a place kept beside the lists.
 */
const SHARED_VERBS = 'lib/components/common/verbs.ts';
const FILE_HOST = 'lib/components/common/FileVerbs.svelte';
/** The doors that draw a file's Add to off `menu`, beside the bar, which draws it off `bar`. */
const MENU_DOORS = ['lib/components/AssetView.svelte', 'lib/components/theater/CellMenu.svelte'];

function everySource(from: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(from)) {
		const path = join(from, entry);
		if (statSync(path).isDirectory()) {
			// The component gallery is a separate checkout, and not part of the product.
			if (relative(SOURCE, path).replaceAll('\\', '/') !== 'routes/design') {
				found.push(...everySource(path));
			}
		} else if (/\.(svelte|ts)$/.test(entry) && !/\.test\.(ts|svelte)$/.test(entry)) {
			found.push(path);
		}
	}
	return found;
}

describe('one Add to menu, and no second one', () => {
	const sources = everySource(SOURCE).map((path) => ({
		at: relative(SOURCE, path).replaceAll('\\', '/'),
		text: readFileSync(path, 'utf8')
	}));
	const read = (at: string): string => sources.find((one) => one.at === at)?.text ?? '';

	it('finds the sources it reads', () => {
		// A survey that matched nothing would pass every rule below it in silence.
		for (const at of [SHARED_VERBS, FILE_HOST, ...MENU_DOORS]) expect(read(at), at).not.toBe('');
	});

	it('declares the door once, in the shared builder', () => {
		const declared = sources
			.filter(({ text }) => /\blabel:\s*['"`]Add to['"`]/.test(text))
			.map(({ at }) => at);
		expect(declared).toEqual([SHARED_VERBS]);
	});

	it('draws no Add to row by hand anywhere', () => {
		// A row written in markup is a door the declaration cannot reach: its rows, its lists and
		// its words are whatever that file says.
		const byHand = sources
			.filter(({ text }) => /<(ContextMenuItem|PickMenu)\b[^>]*\blabel="Add to"/.test(text))
			.map(({ at }) => at);
		expect(byHand).toEqual([]);
	});

	it('keeps no sheet for a place beside the lists in the file host', () => {
		// A sheet per place beside the lists would ask again what the flyout already asks.
		expect(read(FILE_HOST)).not.toMatch(/<PickDialog\b/);
	});

	it('hands the bar the same list the menu reads', () => {
		// Both from `built`, so nothing can take the lists off one of them on the way out.
		const host = read(FILE_HOST);
		expect(host).toMatch(/\bbar: \(ids: string\[\]\) => built\(ids\),/);
		expect(host).toMatch(/\bmenu: \(ids: string\[\], subjectId\?: string\) => menuVerbs\(built\(/);
	});

	for (const at of MENU_DOORS) {
		it(`draws its Add to off the declared door: ${at}`, () => {
			const text = read(at);
			expect(text).toMatch(/verbs\.menu\(/);
			expect(text).toMatch(/\.find\(\(one\) => one\.id === 'add'\)/);
			expect(text).toMatch(/<VerbMenuItems\b/);
		});
	}
});
