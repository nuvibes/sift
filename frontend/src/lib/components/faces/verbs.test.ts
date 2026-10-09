/*
 * The face surfaces' bars and menus cannot offer different things: the declaration, and the markup
 * drawing it through the shared renderers. The strip's `noMenu` is asserted, not skipped.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { barVerbs, menuVerbs } from '$lib/components/common/verbs';
import { DISCARD_ICON, faceVerbs, pileVerbs, type FaceVerbHandlers } from './verbs';

const NOTHING = () => {};

/** A surface supporting everything, `showGroup` included, so no verb escapes the rules below. */
const EVERYTHING: FaceVerbHandlers = {
	showGroup: NOTHING,
	name: NOTHING,
	agree: NOTHING,
	undo: NOTHING,
	move: NOTHING,
	setAside: NOTHING,
	restore: NOTHING,
	remove: NOTHING
};

describe('the declared face verbs', () => {
	it('builds every verb a surface hands a function for, benign first', () => {
		expect(faceVerbs(EVERYTHING).map((verb) => verb.id)).toEqual([
			'show-group',
			'name',
			'agree',
			'undo',
			'move',
			'set-aside',
			'restore',
			'remove'
		]);
	});

	it('offers nothing a surface handed no function for', () => {
		const pile = faceVerbs({ name: NOTHING, move: NOTHING, setAside: NOTHING, remove: NOTHING });
		expect(pile.map((verb) => verb.id)).toEqual(['name', 'move', 'set-aside', 'remove']);
	});

	it('draws Discard with the minus on a face and on a pile alike, never the Hide eye', () => {
		// The Discard glyph is `remove`; `visibility_off` is Hide.
		const discard = (verbs: { id: string; icon: string }[]) =>
			verbs.find((verb) => verb.id === 'set-aside')?.icon;
		expect(DISCARD_ICON).toBe('remove');
		expect(discard(faceVerbs(EVERYTHING))).toBe(DISCARD_ICON);
		expect(discard(pileVerbs({ setAside: NOTHING }))).toBe(DISCARD_ICON);
	});

	it('puts the one that destroys something last, and marks it', () => {
		const all = faceVerbs(EVERYTHING);
		expect(all[all.length - 1].id).toBe('remove');
		expect(all.filter((verb) => verb.destructive).map((verb) => verb.id)).toEqual(['remove']);
	});

	it('gives the bar and the menu the same list', () => {
		// Nothing is `singleOnly`, so the two surfaces are identical.
		const all = faceVerbs(EVERYTHING);
		expect(barVerbs(all).map((verb) => verb.id)).toEqual(menuVerbs(all).map((verb) => verb.id));
	});

	it('says what a face verb is called, so two surfaces cannot word it differently', () => {
		const labels = new Map(faceVerbs(EVERYTHING).map((verb) => [verb.id, verb.label]));
		expect(labels.get('agree')).toBe('Confirm name');
		expect(labels.get('undo')).toBe('Remove the name from this');
		expect(
			new Map(
				faceVerbs({ ...EVERYTHING, who: 'Cassia Lynn' }).map((verb) => [verb.id, verb.label])
			).get('undo')
		).toBe('Remove Cassia Lynn from this');
		// The id stays `show-group`: code addresses the row by it.
		expect(labels.get('show-group')).toBe('Show the face group');
		// A face deleted ceases to exist; the id stays `remove`.
		expect(labels.get('remove')).toBe('Delete');
	});
});

describe('the declared pile verbs', () => {
	it('offers the naming row dim, saying where the naming is done', () => {
		// Named against ONE group on its card, so the bar's row points there.
		const [name] = pileVerbs({ nameElsewhere: 'Name a group from its own card' });
		expect(name.id).toBe('name');
		expect(name.disabled).toBe(true);
		expect(name.why).toBe('Name a group from its own card');
		expect(name.run).toBeUndefined();
	});

	it('lets a wall offer BOTH answers about a grouping and dim the one that does not apply', () => {
		const both = pileVerbs({ setAside: NOTHING, restore: NOTHING, remove: NOTHING });
		expect(both.map((verb) => verb.id)).toEqual(['set-aside', 'restore', 'remove']);
	});

	it('calls the verb that ends a grouping and its faces Delete', () => {
		const [remove] = pileVerbs({ remove: NOTHING });
		expect(remove.label).toBe('Delete');
		expect(remove.destructive).toBe(true);
	});

	it('gives a real handler a live row rather than a dim one', () => {
		const [name] = pileVerbs({ name: NOTHING, nameElsewhere: 'ignored' });
		expect(name.disabled).toBe(false);
		expect(name.why).toBeUndefined();
		expect(name.run).toBe(NOTHING);
	});
});

describe('no face surface writes a verb of its own', () => {
	/**
	 * Every surface where a face or a pile has verbs. Not here: `MoveFacesDialog` (crops are
	 * destinations), `FolderSuggestions` (three answers), `organize/Thumb` (a record with its own
	 * Undo).
	 */
	const SURFACES: {
		what: string;
		path: string;
		renderers: string[];
		/** Why it answers a right-click with nothing of Sift's, asserted absent below. */
		noMenu?: string;
	}[] = [
		{
			what: 'the pile',
			path: 'src/lib/components/faces/PileDetail.svelte',
			renderers: ['<VerbButtons', '<VerbMenuItems']
		},
		{
			what: 'the wall of piles',
			path: 'src/lib/components/faces/FaceGroups.svelte',
			renderers: ['<VerbButtons', '<VerbMenuItems']
		},
		{
			what: "a person's identified wall",
			path: 'src/lib/components/organize/IdentifiedForPerson.svelte',
			renderers: ['<VerbButtons', '<VerbMenuItems']
		},
		{
			what: 'the face strip under a file',
			path: 'src/lib/components/FacesInThis.svelte',
			// The LIST is the declared one, drawn as the card's buttons.
			renderers: ['faceVerbs('],
			noMenu:
				'every verb this face has is a labelled button on its own card, so a menu offering the ' +
				'same five rows is a hidden copy of what is already on screen. A right-click menu ' +
				'earns a surface that has more verbs than it can show, or a selection to address ' +
				'them at; this card has neither.'
		}
	];

	it.each(SURFACES)('$what renders the declared list', ({ path, renderers }) => {
		const source = readFileSync(path, 'utf8');
		for (const renderer of renderers) expect(source).toContain(renderer);
	});

	it.each(SURFACES)('$what writes no menu row of its own', ({ path }) => {
		const source = readFileSync(path, 'utf8');
		expect(source, 'a row was written into this surface instead of declared as a verb').not.toMatch(
			/<ContextMenuItem/
		);
	});

	it.each(SURFACES)('$what answers a right-click at all', ({ path, noMenu }) => {
		// Without the shared menu a face's right-click is the browser's.
		const source = readFileSync(path, 'utf8');
		// Anchored on what ENDS the tag: `<ContextMenuItem` would satisfy a plain match.
		if (noMenu !== undefined) {
			expect(source, noMenu).not.toMatch(/<ContextMenu[\s>]/);
			return;
		}
		expect(source).toMatch(/<ContextMenu[\s>]/);
	});

	it('picks the thing under the pointer before opening on it', () => {
		// The menu acts without picking, which on the pile would raise a form nobody could see.
		// Read off the table, never by counting.
		const withAMenu = SURFACES.filter((surface) => surface.noMenu === undefined);
		expect(withAMenu.length).toBeGreaterThan(0);
		for (const { path } of withAMenu) {
			expect(readFileSync(path, 'utf8'), path).toContain('function aimAt');
		}
	});

	it('checks every face surface that draws a selection bar', () => {
		// Every ActionBar over faces must be named above.
		const bars = SURFACES.filter((surface) =>
			readFileSync(surface.path, 'utf8').includes('<ActionBar')
		);
		expect(bars.length).toBe(3);
	});
});
