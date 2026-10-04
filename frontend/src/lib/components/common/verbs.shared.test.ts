/*
 * A file's menu and a wall's menu open with the same row, and every row the two carry says the
 * same words with the same glyph in the same part of the menu, in the same order. A guest's two
 * menus, which have no door, stand the heart on its own in the same words.
 *
 * Both lists are built at their widest (an admin, every handler) because a row absent from both
 * is not drift, and a narrower case would hide the rows that are. The rows compared are the
 * flattened ones, doors opened, so a row cannot differ by hiding inside Add to.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { fileVerbs, withoutVerbs, type FileVerbHandlers } from '$lib/grid/verbs';
import { entityVerbs, type EntityVerbHandlers } from '$lib/components/entity/verbs';
import { flatVerbs, menuVerbs, type Verb, type VerbPick } from './verbs';

const noop = () => {};

/** A list that answers nothing: the five places a file can be put are lists, not presses. */
const PICK: VerbPick = {
	kind: 'tag',
	plural: 'tags',
	ask: async () => ({ choices: [], more: 0 }),
	pick: async () => 'landed'
};

const FILE: FileVerbHandlers = {
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
	keepLocal: noop,
	remove: noop
};

const WALL: EntityVerbHandlers = {
	rename: noop,
	tag: PICK,
	favorite: noop,
	pin: noop,
	rate: noop,
	enrich: noop,
	lookUp: noop,
	keepLocal: noop,
	share: noop,
	visibility: noop,
	hide: noop,
	merge: noop,
	remove: noop
};

const fileMenu = menuVerbs(
	fileVerbs(
		{
			isAdmin: true,
			canSave: true,
			showingHidden: false,
			count: 1,
			canMove: true,
			canCompress: true,
			handlers: FILE
		},
		{ label: () => 'Save', icon: () => 'download' as const }
	)
);
const wallMenu = menuVerbs(entityVerbs({ isAdmin: true, handlers: WALL }));

/* The third menu: a guest's, on a file and on a wall. No door, so the heart is a row of its own. */
const guestFileMenu = menuVerbs(
	fileVerbs(
		{
			isAdmin: false,
			canSave: true,
			showingHidden: false,
			count: 1,
			canMove: false,
			canCompress: false,
			handlers: FILE
		},
		{ label: () => 'Save', icon: () => 'download' as const }
	)
);
const guestWallMenu = menuVerbs(entityVerbs({ isAdmin: false, handlers: WALL }));

/*
 * The one shared row whose words differ on purpose: a file's opens "Remove" (the file stays on
 * disk), a wall's "Delete" (the record goes). Its place, last and alone, is still held below.
 */
const WORDED_APART = new Set(['delete']);

function byId(verbs: readonly Verb[]): Map<string, Verb> {
	return new Map(flatVerbs(verbs).map((verb) => [verb.id, verb]));
}

describe("a file's menu and a wall's menu", () => {
	it('open with the same row', () => {
		expect(fileMenu[0]?.id).toBe('add');
		expect(wallMenu[0]?.id).toBe(fileMenu[0]?.id);
		expect(wallMenu[0]?.label).toBe(fileMenu[0]?.label);
	});

	it('both carry the heart, the tag, the pin, the stars, the three enrich rows and sharing', () => {
		const file = byId(fileMenu);
		const shared = [...byId(wallMenu).keys()].filter((id) => file.has(id));
		expect(shared).toEqual(
			expect.arrayContaining([
				'tag',
				'favorite',
				'pin',
				'rate',
				'auto-enrich',
				'enrich',
				'keep-local',
				'share',
				'visibility',
				'hide',
				'delete'
			])
		);
	});

	it('say the same words with the same glyph in the same part for every row both carry', () => {
		const file = byId(fileMenu);
		for (const [id, row] of byId(wallMenu)) {
			const twin = file.get(id);
			if (!twin || WORDED_APART.has(id)) continue;
			expect([row.group, row.label, row.icon], id).toEqual([twin.group, twin.label, twin.icon]);
		}
	});

	it('draw the rows both carry in the same order', () => {
		const file = flatVerbs(fileMenu).map((verb) => verb.id);
		const wall = flatVerbs(wallMenu).map((verb) => verb.id);
		expect(wall.filter((id) => file.includes(id))).toEqual(file.filter((id) => wall.includes(id)));
	});
});

describe("a guest's menus", () => {
	it('stand the heart on its own in the same whole instruction on a file and on a wall', () => {
		const file = byId(guestFileMenu).get('favorite');
		const wall = byId(guestWallMenu).get('favorite');
		expect(file?.label).toBe('Add to favorites');
		expect([wall?.label, wall?.icon, wall?.group]).toEqual([file?.label, file?.icon, file?.group]);
		expect(guestFileMenu.map((verb) => verb.id)).not.toContain('add');
	});
});

describe('no menu', () => {
	it('carries a row twice, doors opened', () => {
		for (const [name, menu] of Object.entries({
			fileMenu,
			wallMenu,
			guestFileMenu,
			guestWallMenu
		})) {
			const rows = flatVerbs(menu);
			const ids = rows.map((verb) => verb.id);
			const labels = rows.map((verb) => verb.label);
			expect(new Set(ids).size, `${name}: ${ids.join(', ')}`).toBe(ids.length);
			expect(new Set(labels).size, `${name}: ${labels.join(', ')}`).toBe(labels.length);
		}
	});

	it('keeps a verb its screen took away inside a door', () => {
		/* Loops draws a Tag of its own (the loop's, not the file's) and takes the file's away. The
		   file's Tag lives inside Add to, so taking it away has to reach in there. */
		const loops = flatVerbs(withoutVerbs(fileMenu, ['tag', 'delete']));
		expect(loops.map((verb) => verb.id)).not.toContain('tag');
		expect(loops.map((verb) => verb.id)).toContain('collect');
		const emptied = withoutVerbs(fileMenu, [
			'assign',
			'site',
			'collect',
			'photo_set',
			'tag',
			'song',
			'favorite'
		]);
		expect(emptied.map((verb) => verb.id)).not.toContain('add');
		// And the grid takes a wall's verbs away through it, on the bar and the menu alike.
		expect(readFileSync('src/lib/components/AssetGrid.svelte', 'utf8')).toContain(
			'withoutVerbs(all, hideVerbs)'
		);
	});
});

describe('the rows both menus carry', () => {
	it("are made by the shared builders, never declared again in the file's menu", () => {
		// One declaration, so a shared row's words, glyph and part cannot come apart between the
		// two menus.
		const source = readFileSync('src/lib/grid/verbs.ts', 'utf8');
		for (const row of ["id: 'add'", "id: 'favorite'", "id: 'pin'", "id: 'rate'"]) {
			expect(source, row).not.toContain(row);
		}
		for (const builder of ['addToVerb(', 'favoriteVerb(', 'pinVerb(', 'ratingVerb(']) {
			expect(source, builder).toContain(builder);
		}
	});
});
