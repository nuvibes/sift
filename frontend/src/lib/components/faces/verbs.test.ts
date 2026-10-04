/*
 * The face surfaces' bars and their right-click menus cannot come to offer different things.
 *
 * The same two halves the file grid's test and the entity walls' test have, because neither half
 * can see what the other catches:
 *
 * 1. The declaration: every verb a surface hands a function for is built, in one order, with the
 *    destructive one last so the menu's separator lands in front of it.
 * 2. The markup: every face surface renders the declaration through the two shared renderers and
 *    hand-writes no rows or buttons, which catches a button added straight into one wall's bar.
 *
 * One surface has no menu, written down as a property rather than a hole. The strip under a file
 * draws every verb a face has as a button on the card, so a menu would be a second door onto the
 * same rows. `noMenu` says so, and the rule demands the absence of a menu there; an excuse nothing
 * checks would outlive what it was written about.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { barVerbs, menuVerbs } from '$lib/components/common/verbs';
import { DISCARD_ICON, faceVerbs, pileVerbs, type FaceVerbHandlers } from './verbs';

const NOTHING = () => {};

/** A surface that supports everything a face can be told. No real one does; that is the point.
 *
 *  `showGroup` is in it too: a verb left out here would be hidden from every rule below (the
 *  order, the split and the words). */
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
		// The pile: it can name and it has nothing to agree with, because nothing in it is named.
		const pile = faceVerbs({ name: NOTHING, move: NOTHING, setAside: NOTHING, remove: NOTHING });
		expect(pile.map((verb) => verb.id)).toEqual(['name', 'move', 'set-aside', 'remove']);
	});

	it('draws Discard with the minus on a face and on a pile alike, never the Hide eye', () => {
		// The Discard glyph is `remove` app-wide. `visibility_off` is the Hide verb and the hidden
		// mark everywhere else, and a discarded face is not hidden.
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
		// Nothing here is `singleOnly`: every face verb means the same thing pointed at one face or
		// at forty. So the two surfaces are identical, and if a verb is ever kept off the bar this
		// is what fails.
		const all = faceVerbs(EVERYTHING);
		expect(barVerbs(all).map((verb) => verb.id)).toEqual(menuVerbs(all).map((verb) => verb.id));
	});

	it('says what a face verb is called, so two surfaces cannot word it differently', () => {
		const labels = new Map(faceVerbs(EVERYTHING).map((verb) => [verb.id, verb.label]));
		// The two that say what the press does to a name, which "Agree" and "Undo" over a crop of
		// somebody's face do not; these are the vocabulary's verbs for the two acts.
		expect(labels.get('agree')).toBe('Confirm name');
		expect(labels.get('undo')).toBe('Remove the name from this');
		expect(
			new Map(
				faceVerbs({ ...EVERYTHING, who: 'Cassia Lynn' }).map((verb) => [verb.id, verb.label])
			).get('undo')
		).toBe('Remove Cassia Lynn from this');
		// Which group: this row is read beside verbs about a person and a file, so it says "face
		// group". The id stays `show-group`: it is what the code addresses the row by, and an id
		// that follows a label is one nothing can rely on.
		expect(labels.get('show-group')).toBe('Show the face group');
		// A face deleted ceases to exist, so the word is Delete, not Remove. The id stays `remove`;
		// see the note in `verbs.ts`.
		expect(labels.get('remove')).toBe('Delete');
	});
});

describe('the declared pile verbs', () => {
	it('offers the naming row dim, saying where the naming is done', () => {
		// A pile is named against ONE group with a field on its card, so a bar addressed at a
		// selection cannot do it. Dropping the row would read as a feature that does not exist.
		const [name] = pileVerbs({ nameElsewhere: 'Name a group from its own card' });
		expect(name.id).toBe('name');
		expect(name.disabled).toBe(true);
		expect(name.why).toBe('Name a group from its own card');
		expect(name.run).toBeUndefined();
	});

	it('lets a wall offer BOTH answers about a grouping and dim the one that does not apply', () => {
		// Unlike a face. The wall's bar must not change shape under somebody switching tabs.
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
	 * Every surface where a face or a pile is an object with things that can be done to it.
	 *
	 * Named, so a new one is a visible omission. Three surfaces are deliberately NOT here and each
	 * has a reason that is not "nobody got round to it":
	 *
	 * - `MoveFacesDialog` draws crops that are DESTINATIONS (the groups these faces might move
	 *   into) so a menu there would offer verbs on the thing being chosen between.
	 * - `FolderSuggestions` is three answers and no fourth, in its own words, and a menu on the
	 *   crop illustrating a folder's question would be that fourth.
	 * - `organize/Thumb` is the picture on a RECORD of something already decided. Its one verb,
	 *   Undo, is a visible button on the row, which is the whole surface rather than a shortcut to
	 *   part of it.
	 */
	const SURFACES: {
		what: string;
		path: string;
		renderers: string[];
		/**
		 * Why this surface answers a right-click with NOTHING of Sift's. Absent where it answers.
		 *
		 * A surface that has no menu is not a gap to be filled in later, so the excuse is checked
		 * in the other direction rather than skipped: the rule below asserts the shared menu is
		 * absent, which is what stops a sentence here outliving the thing it describes.
		 */
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
			// No renderer: this surface draws the declared list as the buttons on each card, from
			// `faceVerbs` by way of `verbsFor`. What is held is that the LIST is the declared one:
			// a strip that built its own five buttons is the drift this file exists to refuse.
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
		// What this whole file exists for: without the shared menu a face's right-click would be
		// the BROWSER'S, offering "Copy image address" over a raw API path.
		const source = readFileSync(path, 'utf8');
		// Anchored on what ENDS the tag name. A plain `toContain('<ContextMenu')` is satisfied by
		// `<ContextMenuItem`, which the test above forbids, so the two guards would have agreed
		// with each other while the shared menu itself was gone.
		if (noMenu !== undefined) {
			// The one surface that deliberately has none. Asserted ABSENT rather than skipped: an
			// excuse nothing checks is a sentence that survives the day somebody puts the menu back.
			expect(source, noMenu).not.toMatch(/<ContextMenu[\s>]/);
			return;
		}
		expect(source).toMatch(/<ContextMenu[\s>]/);
	});

	it('picks the thing under the pointer before opening on it', () => {
		// On the pile it is load-bearing rather than tidy: naming happens in a field
		// inside the selection bar, and the bar is only on screen while something is picked, so a
		// menu that acted without picking would raise a form nobody could see.
		//
		// Asked of the surfaces that HAVE a menu, read off the table rather than off a slice of it:
		// `slice(0, 3)` said the same thing by counting, and a count is what stops being true when a
		// surface is added in the middle.
		const withAMenu = SURFACES.filter((surface) => surface.noMenu === undefined);
		expect(withAMenu.length).toBeGreaterThan(0);
		for (const { path } of withAMenu) {
			expect(readFileSync(path, 'utf8'), path).toContain('function aimAt');
		}
	});

	it('checks every face surface that draws a selection bar', () => {
		// A list of surfaces beside the thing it describes drifts, and a short list is
		// indistinguishable from a finished one. Every file under the face and organize folders that
		// raises an ActionBar over faces has to be named above.
		const bars = SURFACES.filter((surface) =>
			readFileSync(surface.path, 'utf8').includes('<ActionBar')
		);
		expect(bars.length).toBe(3);
	});
});
