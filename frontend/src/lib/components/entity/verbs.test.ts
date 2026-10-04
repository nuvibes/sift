/*
 * The four walls' bars and their right-click menus cannot come to offer different things.
 *
 * The same two halves the file grid's test has, and for the same reason: one of them cannot see
 * what the other catches:
 *
 * 1. The DECLARATION: every verb a wall hands a function for reaches both surfaces, except the
 *    ones that say in so many words that they only make sense pointed at one row.
 * 2. The MARKUP: the bar and every one of the four menus render the declaration through the two
 *    renderers and hand-write no rows of their own. This is the half that catches somebody adding
 *    an item straight into one wall's menu, so the menu grows a verb the bar never does.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	barShape,
	barVerbs,
	flatVerbs,
	menuVerbs,
	type VerbPick
} from '$lib/components/common/verbs';
import { entityVerbs, type EntityVerbHandlers } from './verbs';
import { EVERY_BOX } from '$lib/entity/enrichment.svelte';

const NOTHING = () => {};

/** The list of tags, answering nothing: the Tag row is the list itself on the bar and the menu. */
const TAGS: VerbPick = {
	kind: 'tag',
	plural: 'tags',
	ask: async () => ({ choices: [], more: 0 }),
	pick: async () => 'landed'
};

/** A wall that supports everything, which is the People wall. */
const EVERYTHING: EntityVerbHandlers = {
	rename: NOTHING,
	tag: TAGS,
	favorite: NOTHING,
	rate: NOTHING,
	share: NOTHING,
	hide: NOTHING,
	merge: NOTHING,
	remove: NOTHING
};

describe('the declared entity verbs reach both surfaces', () => {
	it('offers every verb a wall hands a function for', () => {
		expect(entityVerbs({ isAdmin: true, handlers: EVERYTHING }).map((verb) => verb.id)).toEqual([
			'rename',
			'add',
			'rate',
			'share',
			'hide',
			'merge',
			'delete'
		]);
	});

	it('offers Merge on BOTH surfaces, pointed at one row or at several', () => {
		/* Somebody who has just selected the two rows they noticed are the same person must not
		 * have to clear the selection, open one of them and find a button there. It is not
		 * `singleOnly`: one row means "fold this into somebody I am about to pick" and several mean
		 * "fold these into one of themselves", and both are the same act with the same guard.
		 */
		const all = entityVerbs({ isAdmin: true, handlers: EVERYTHING });

		expect(menuVerbs(all).map((verb) => verb.id)).toContain('merge');
		expect(barVerbs(all).map((verb) => verb.id)).toContain('merge');
	});

	it('keeps Merge away from an account that cannot change the library', () => {
		const asGuest = entityVerbs({ isAdmin: false, handlers: EVERYTHING });

		expect(asGuest.map((verb) => verb.id)).not.toContain('merge');
	});

	it('offers nothing a wall handed no function for', () => {
		// The Tags wall: no heart, no stars, and a tag cannot carry a tag. What makes this worth
		// asserting is that the ABSENCE has to reach both surfaces equally, which the next test
		// covers: here it is only that a missing handler means a missing verb rather than a verb
		// that does nothing when pressed.
		const bare = entityVerbs({
			isAdmin: true,
			handlers: { rename: NOTHING, share: NOTHING, hide: NOTHING, remove: NOTHING }
		});
		expect(bare.map((verb) => verb.id)).toEqual(['rename', 'share', 'hide', 'delete']);
	});

	it('gives the bar every verb except the ones that need one row', () => {
		const all = entityVerbs({ isAdmin: true, handlers: EVERYTHING });
		const bar = barVerbs(all).map((verb) => verb.id);
		/* Doors opened on both sides: a verb inside Add to is still a verb the bar must reach. */
		const menu = flatVerbs(menuVerbs(all)).map((verb) => verb.id);

		expect([...menu].sort()).toEqual(
			flatVerbs(all)
				.map((verb) => verb.id)
				.sort()
		);
		const missing = menu.filter((id) => !bar.includes(id));
		expect(missing).toEqual(
			flatVerbs(all)
				.filter((verb) => verb.singleOnly)
				.map((verb) => verb.id)
		);

		// And the excuse itself is named, not merely present. Without this, keeping a verb off the
		// bar is one word away: mark it `singleOnly` and the assertion above agrees with itself.
		// One row only makes sense pointed at one thing, and it is this one.
		expect(missing).toEqual(['rename']);
	});

	it('takes the same verbs away from a guest on both surfaces', () => {
		const asGuest = entityVerbs({ isAdmin: false, handlers: EVERYTHING });
		const bar = new Set(barVerbs(asGuest).map((verb) => verb.id));
		for (const verb of menuVerbs(asGuest)) {
			if (verb.singleOnly) continue;
			expect(bar.has(verb.id), `${verb.id} is in the menu but not the bar`).toBe(true);
		}
		// Hiding is theirs; the three that change the library for everybody are not.
		expect(asGuest.map((verb) => verb.id)).toContain('hide');
		expect(asGuest.map((verb) => verb.id)).not.toContain('delete');
		expect(asGuest.map((verb) => verb.id)).not.toContain('share');
		expect(asGuest.map((verb) => verb.id)).not.toContain('tag');
	});

	it('puts the stars behind a press, the same as the file grid', () => {
		// One shape everywhere: the rating is a single button in the bar, as on the file grid,
		// rather than five inline stars in a strip that already carries six other verbs.
		const rate = entityVerbs({ isAdmin: true, handlers: EVERYTHING }).find(
			(verb) => verb.id === 'rate'
		);
		expect(rate?.stars).toBe(true);
		expect(rate?.flyout).toBe(true);
	});

	it('draws the Tag row as the list on the bar and the menu alike, with no sheet beside it', () => {
		const verbs = entityVerbs({ isAdmin: true, handlers: EVERYTHING });
		const tag = flatVerbs(verbs).find((verb) => verb.id === 'tag');
		expect(tag?.pick).toBe(TAGS);
		expect(tag?.run).toBeUndefined();
		const onBar = flatVerbs(barVerbs(verbs)).find((verb) => verb.id === 'tag');
		expect(onBar?.pick).toBe(TAGS);
	});

	it('opens with the door a file menu opens with, holding the tag and the heart', () => {
		const all = menuVerbs(entityVerbs({ isAdmin: true, handlers: EVERYTHING }));
		expect(all[0].id).toBe('add');
		expect(all[0].label).toBe('Add to');
		expect(all[0].children?.map((verb) => verb.id)).toEqual(['tag', 'favorite']);
		expect(all[0].children?.find((verb) => verb.id === 'favorite')?.label).toBe('Favorites');
	});

	it('gives the heart a row of its own where there is nothing else to put the thing on', () => {
		// A guest cannot tag, and a tag carries no tag: a door onto one row is a press for nothing.
		const asGuest = entityVerbs({ isAdmin: false, handlers: EVERYTHING });
		expect(asGuest.map((verb) => verb.id)).not.toContain('add');
		expect(asGuest.find((verb) => verb.id === 'favorite')?.label).toBe('Add to favorites');
	});

	it('says Remove from favorites where everything is one already', () => {
		const hearted = entityVerbs({ isAdmin: false, favorite: true, handlers: EVERYTHING });
		expect(hearted.find((verb) => verb.id === 'favorite')?.label).toBe('Remove from favorites');
		const inside = entityVerbs({ isAdmin: true, favorite: true, handlers: EVERYTHING });
		const door = inside.find((verb) => verb.id === 'add');
		expect(door?.children?.find((verb) => verb.id === 'favorite')?.label).toBe(
			'Remove from favorites'
		);
	});

	it('points the hide verb the other way where everything is already hidden', () => {
		const inHidden = entityVerbs({ isAdmin: true, showingHidden: true, handlers: EVERYTHING });
		expect(inHidden.find((verb) => verb.id === 'hide')?.label).toBe('Unhide');
	});
});

describe('no wall writes a verb of its own', () => {
	/** The bar, and the menu of each of the four walls. Named, so a new wall is a visible omission. */
	const SURFACES: { what: string; path: string; renderer: string }[] = [
		{
			what: 'the shared bar',
			path: 'src/lib/components/entity/EntitySelectionBar.svelte',
			renderer: '<VerbButtons'
		},
		{
			what: 'the People wall',
			path: 'src/routes/people/+page.svelte',
			renderer: '<VerbMenuItems'
		},
		{
			what: 'the Sites wall',
			path: 'src/routes/sites/+page.svelte',
			renderer: '<VerbMenuItems'
		},
		{
			what: 'the Collections wall',
			path: 'src/routes/collections/+page.svelte',
			renderer: '<VerbMenuItems'
		},
		{ what: 'the Tags wall', path: 'src/routes/tags/+page.svelte', renderer: '<VerbMenuItems' }
	];

	it.each(SURFACES)('$what renders the declared list', ({ path, renderer }) => {
		const source = readFileSync(path, 'utf8');
		expect(source).toContain(renderer);
	});

	it.each(SURFACES.slice(1))('$what writes no menu row of its own', ({ path }) => {
		const source = readFileSync(path, 'utf8');
		// A hand-written row is the drift. There is deliberately no allowance for one: a verb one
		// wall can offer and its bar cannot belongs in the declaration, with a reason on it.
		expect(source, 'a row was written into this wall instead of declared as a verb').not.toMatch(
			/<ContextMenuItem/
		);
	});

	it('the shared bar writes no button of its own', () => {
		const source = readFileSync(SURFACES[0].path, 'utf8');
		expect(source).not.toMatch(/<button/);
	});

	it('checks every wall there is', () => {
		// A list of surfaces beside the thing it describes drifts, and a short list is
		// indistinguishable from a finished one. So it is checked against the walls that actually
		// draw the shared bar: a fifth wall fails here until it is named above.
		const walls = ['people', 'sites', 'collections', 'tags'].filter((name) =>
			readFileSync(`src/routes/${name}/+page.svelte`, 'utf8').includes('<EntitySelectionBar')
		);
		expect(SURFACES.length).toBe(walls.length + 1);
	});
});

/*
 * The pin, which is the one verb in the list whose LABEL reverses.
 *
 * Hide does the same and is driven by a fact about the WALL (is it showing hidden rows); this one
 * is driven by a fact about the ROWS, which nothing but the caller can know. That difference is
 * where the wrong answer is easy to write, so it is what these check.
 */
describe('the pin verb', () => {
	const PINNABLE: EntityVerbHandlers = { pin: NOTHING };

	it('is offered to everybody, not only an admin', () => {
		// It is this account's own opinion and moves nobody else's screen, exactly like the heart.
		const ids = entityVerbs({ isAdmin: false, handlers: PINNABLE }).map((verb) => verb.id);
		expect(ids).toEqual(['pin']);
	});

	it('says Pin on something that is not pinned, and Unpin on something that is', () => {
		const off = entityVerbs({ isAdmin: true, handlers: PINNABLE })[0];
		const on = entityVerbs({ isAdmin: true, pinned: true, handlers: PINNABLE })[0];
		expect(off.label).toBe('Pin');
		expect(on.label).toBe('Unpin');
	});

	it('turns the icon over with the label', () => {
		/* A row reading "Unpin" beside the same pin the row above it uses for "Pin" is a row you have
		   to read the words of. The pair exists in the icon set for this. */
		expect(entityVerbs({ isAdmin: true, handlers: PINNABLE })[0].icon).toBe('keep');
		expect(entityVerbs({ isAdmin: true, pinned: true, handlers: PINNABLE })[0].icon).toBe(
			'keep_off'
		);
	});

	it('asks for the OPPOSITE of what they are, rather than toggling each row', () => {
		const asked: boolean[] = [];
		const verbs = entityVerbs({
			isAdmin: true,
			pinned: true,
			handlers: { pin: (_ids, wanted) => asked.push(wanted) }
		});
		verbs[0].run?.(['a', 'b']);
		expect(asked).toEqual([false]);
	});

	it('is not offered at all on a wall that hands over no pin', () => {
		expect(entityVerbs({ isAdmin: true, handlers: EVERYTHING }).map((v) => v.id)).not.toContain(
			'pin'
		);
	});
});

/*
 * Refused on the row rather than on the press: anything with enrichment disabled shows the
 * enrichment options greyed out.
 */
describe('a subject kept local', () => {
	const ENRICHING: EntityVerbHandlers = { enrich: NOTHING, lookUp: NOTHING, keepLocal: NOTHING };

	it('greys BOTH enrich rows and says why under each', () => {
		const verbs = entityVerbs({
			isAdmin: true,
			enrichRefused: true,
			enrichWhy: 'Kept local',
			handlers: ENRICHING
		});
		for (const id of ['auto-enrich', 'enrich']) {
			const row = verbs.find((verb) => verb.id === id);
			expect(row?.disabled, id).toBe(true);
			expect(row?.why, id).toBe('Kept local');
		}
	});

	it('takes the flyout away with the press, so a box cannot be asked one level down', () => {
		const verbs = entityVerbs({
			isAdmin: true,
			enrichRefused: true,
			enrichBoxes: [{ word: 'stashdb', name: 'StashDB' }],
			handlers: ENRICHING
		});
		expect(verbs.find((verb) => verb.id === 'auto-enrich')?.children).toBeUndefined();
	});

	it('hands each Auto-enrich row its own box on, so a per-box row asks that box', () => {
		const asked: (string | undefined)[] = [];
		const verbs = entityVerbs({
			isAdmin: true,
			enrichBoxes: [{ word: 'stashdb', name: 'StashDB' }],
			handlers: { ...ENRICHING, enrich: (_ids, box) => asked.push(box) }
		});
		for (const row of verbs.find((verb) => verb.id === 'auto-enrich')?.children ?? []) {
			row.run?.(['p1']);
		}
		expect(asked).toEqual([EVERY_BOX, 'stashdb']);
		const named = barShape(verbs).named.map((verb) => verb.id);
		expect(named).toEqual(expect.arrayContaining(['auto-enrich', 'enrich']));
	});

	it('still offers the switch, reversed, because that is the way back', () => {
		const verbs = entityVerbs({
			isAdmin: true,
			keptLocal: true,
			enrichRefused: true,
			handlers: ENRICHING
		});
		const row = verbs.find((verb) => verb.id === 'keep-local');
		expect(row?.label).toBe('Allow enrichment');
		expect(row?.disabled).toBeUndefined();
	});

	it('greys nothing where nothing refuses it', () => {
		const verbs = entityVerbs({ isAdmin: true, handlers: ENRICHING });
		expect(verbs.filter((verb) => verb.disabled)).toEqual([]);
		expect(verbs.find((verb) => verb.id === 'keep-local')?.label).toBe("Don't enrich");
	});
});

describe('Merge is on the bar rather than behind More', () => {
	/*
	 * Merge is drawn with one card selected: `barShape` names the primary rows and folds the rest
	 * behind More, so without its flag Merge would be two presses away on a bar with room to spare.
	 *
	 * Held here rather than as a name inside `barShape`, which is one rule for every wall; a rule
	 * that knows one verb's id is not a rule.
	 */
	it("names Merge among the bar's drawn verbs for an admin", () => {
		const shape = barShape(entityVerbs({ isAdmin: true, handlers: EVERYTHING }));
		expect(shape.named.map((verb) => verb.id)).toContain('merge');
		expect(shape.rest.map((verb) => verb.id)).not.toContain('merge');
	});

	it('offers it to nobody who cannot merge', () => {
		// A guest has no merge handler at all, so there is nothing to name: the same rule that
		// decides whether the verb exists decides whether the bar draws it.
		const shape = barShape(entityVerbs({ isAdmin: false, handlers: EVERYTHING }));
		expect(shape.named.map((verb) => verb.id)).not.toContain('merge');
		expect(shape.rest.map((verb) => verb.id)).not.toContain('merge');
	});
});
