/*
 * What can be done to a named thing, wherever its card is drawn.
 *
 * Walls that each write their own handlers drift: a person's card would offer eight verbs on the
 * People wall and one on a tag's People tab. So what is held here is that the answer
 * comes from the kind and not from the screen, and that the two writes with a quirk of their own
 * still carry it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError } from '$lib/api/client';
import { libraryChanges } from '$lib/library/changes.svelte';
import { goto } from '$app/navigation';
import { toasts } from '$lib/shell/toasts.svelte';
import { KIND_FACTS, WallVerbs, type WallRow } from './wall-verbs.svelte';
import type { EntityKind } from '$lib/entity/related.svelte';

/* The requests are stood in for and nothing else is: the same arrangement `pinning` uses, and for
   the same reason: a mock built from scratch leaves out the parts a refusal path reaches for. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

const EVERY_KIND: EntityKind[] = ['person', 'site', 'tag', 'collection', 'photo_set'];

function wall(kind: EntityKind, rows: WallRow[] = [{ id: 'r1', name: 'Marla Quist', count: 3 }]) {
	const changed = vi.fn();
	const clear = vi.fn();
	const verbs = new WallVerbs({
		kind: () => kind,
		rows: () => rows,
		changed,
		clear
	});
	return { verbs, changed, clear };
}

beforeEach(() => {
	vi.clearAllMocks();
	/* RESET rather than clear, and the difference is what one of these caught: `clearAllMocks`
	   forgets the CALLS and keeps the implementation, so a refusal set up by the test above goes on
	   refusing in the test below, where it reads as a loop that stopped early. */
	for (const call of [mocked.get, mocked.post, mocked.put, mocked.del]) call.mockReset();
	toasts.clear();
});

describe('which verbs a kind has', () => {
	it.each(EVERY_KIND)('%s can be renamed, shared, reported on, hidden and deleted', (kind) => {
		/* The five every named thing has, on every wall. */
		const { verbs } = wall(kind);
		const handlers = verbs.handlers;
		expect(Object.keys(handlers)).toEqual(
			expect.arrayContaining(['rename', 'share', 'visibility', 'hide', 'remove'])
		);
	});

	it('a tag carries no tag and no merge', () => {
		// A tag cannot be put on a tag and two tags do not turn out to be one, so the rows are
		// not drawn rather than drawn and refused.
		const handlers = wall('tag').verbs.handlers;
		expect(handlers.tag).toBeUndefined();
		expect(handlers.merge).toBeUndefined();
		// It IS a thing a stash-box knows about, which is why the pair is offered.
		expect(handlers.enrich).toBeDefined();
		expect(handlers.lookUp).toBeDefined();
	});

	it('a song is shared, reported on and hidden like every other named thing, and merges', () => {
		/* The grant and hide tables know a song, so its card offers Share, Visibility and Hide
		   as a tag's does, and the share panel opens on it as a `song`. Two songs can be one
		   piece of music, so the merge is offered; no stash-box knows a song and it carries no
		   tag. */
		const { verbs } = wall('song', [{ id: 's1', name: 'Lantern Hum', count: 4 }]);
		const handlers = verbs.handlers;
		expect(handlers.share).toBeDefined();
		expect(handlers.visibility).toBeDefined();
		expect(handlers.hide).toBeDefined();
		expect(handlers.tag).toBeUndefined();
		expect(handlers.enrich).toBeUndefined();
		expect(handlers.merge).toBeDefined();
		expect(Object.keys(handlers)).toEqual(
			expect.arrayContaining(['rename', 'remove', 'favorite', 'rate'])
		);
		verbs.askToShare(['s1']);
		expect(verbs.shareOpen).toBe(true);
		expect(verbs.sharing).toEqual([{ type: 'song', id: 's1', label: 'Lantern Hum' }]);
	});

	it("hides a song through the song's own vault route", async () => {
		const { verbs } = wall('song', [{ id: 's1', name: 'Lantern Hum', count: 4 }]);
		mocked.put.mockResolvedValue(undefined);
		await verbs.hideAll(['s1'], true);
		expect(mocked.put).toHaveBeenCalledWith('/songs/s1/vault', { body: { vault: true } });
	});

	it('renames, hearts and deletes a song at its own address', async () => {
		const { verbs } = wall('song', [{ id: 's1', name: 'Lantern Hum', count: 4 }]);
		mocked.put.mockResolvedValue({});
		mocked.del.mockResolvedValue(undefined);
		verbs.askToRename(['s1']);
		verbs.renameTo = 'Lantern Hum (live)';
		await verbs.rename();
		expect(mocked.put).toHaveBeenCalledWith('/songs/s1', { body: { name: 'Lantern Hum (live)' } });
		await verbs.favoriteAll(['s1']);
		expect(mocked.put).toHaveBeenCalledWith('/songs/s1/favorite', { body: { favorite: true } });
		verbs.askToDelete(['s1']);
		expect(verbs.deleteConsequence).toContain('4 files');
		await verbs.remove();
		expect(mocked.del).toHaveBeenCalledWith('/songs/s1');
	});

	it('says every picked row is a favorite only when every one is', () => {
		const { verbs } = wall('person', [
			{ id: 'a', name: 'Ada Byron', favorite: true },
			{ id: 'b', name: 'Bryn Calloway', favorite: false }
		]);
		expect(verbs.allFavorite(['a'])).toBe(true);
		expect(verbs.allFavorite(['a', 'b'])).toBe(false);
		expect(verbs.allFavorite(['a', 'gone'])).toBe(false);
		expect(verbs.allFavorite([])).toBe(false);
	});

	it('a person carries every one of them', () => {
		const handlers = wall('person').verbs.handlers;
		for (const verb of [
			'rename',
			'tag',
			'favorite',
			'rate',
			'enrich',
			'lookUp',
			'share',
			'visibility',
			'hide',
			'merge',
			'remove'
		]) {
			expect(handlers[verb as keyof typeof handlers], verb).toBeDefined();
		}
	});

	it.each(['tag', 'collection'] as const)(
		'a %s has the heart and the stars its card draws',
		(kind) => {
			/*
			 * A tag and a collection have a heart and a rating too: the server has `/favorite` and
			 * `/rating` routes for both, and their cards draw them, so the menu must offer them as
			 * well.
			 */
			const handlers = wall(kind).verbs.handlers;
			expect(handlers.favorite).toBeDefined();
			expect(handlers.rate).toBeDefined();
		}
	);

	it('keeping local is offered exactly where Enrich is', () => {
		/* On every wall and every tab where Enrich is, never on some of them only. */
		for (const kind of EVERY_KIND) {
			const handlers = wall(kind).verbs.handlers;
			expect(handlers.keepLocal !== undefined, kind).toBe(handlers.enrich !== undefined);
		}
	});

	it('keeping local writes the switch and marks the holder the Enrich rows read', async () => {
		// The reply is marked by id, and the holder's own read of the row answers the same.
		const state = { id: 's1', kept_local: true, refused: true, why: '' };
		mocked.put.mockResolvedValue(state);
		mocked.get.mockResolvedValue(state);
		const { verbs } = wall('site', [{ id: 's1', name: 'QuillMoss' }]);
		const mark = vi.spyOn(verbs.enrichment('site'), 'mark');
		verbs.handlers.keepLocal?.(['s1'], true);
		await vi.waitFor(() => expect(mark).toHaveBeenCalledWith(['s1'], state));
		expect(mocked.put).toHaveBeenCalledWith('/stash-boxes/enrichment/site/s1/keep-local', {
			body: { kept_local: true }
		});
	});

	it('no stash-box has a collection or a Photo Set', () => {
		// Asking about one would be a job that finishes having asked nobody anything.
		expect(wall('collection').verbs.handlers.enrich).toBeUndefined();
		expect(wall('photo_set').verbs.handlers.enrich).toBeUndefined();
	});

	it('a collection and a Photo Set can be merged with nothing', () => {
		expect(KIND_FACTS.collection.mergeable).toBeNull();
		expect(KIND_FACTS.photo_set.mergeable).toBeNull();
	});
});

describe('renaming', () => {
	it('draws the new name on the wall at the press, and puts it back on a refusal', async () => {
		const { people } = await import('$lib/people/people.svelte');
		people.items = [{ id: 'r1', name: 'Marla Quist' } as (typeof people.items)[number]];
		let refuse: (error: unknown) => void = () => {};
		mocked.put.mockReturnValueOnce(new Promise((_, reject) => (refuse = reject)) as never);
		const { verbs } = wall('person');
		verbs.askToRename(['r1']);
		verbs.renameTo = 'Ines Dray';
		const renaming = verbs.rename();
		expect(people.items[0]?.name).toBe('Ines Dray');
		refuse(new ApiError(500, 'no'));
		await renaming;
		expect(people.items[0]?.name).toBe('Marla Quist');
		people.items = [];
	});

	it('sends the name alone, so nothing else about the row is replaced', async () => {
		/* The routes read an absent field as "leave it alone". A rename that sent the vault flag it
		   happened to be holding would write a stale one back, in the direction nobody notices,
		   because the row simply reappears. */
		const { verbs, changed } = wall('person');
		verbs.askToRename(['r1']);
		verbs.renameTo = 'Ines Dray';
		await verbs.rename();

		expect(mocked.put).toHaveBeenCalledWith('/people/r1', { body: { name: 'Ines Dray' } });
		expect(changed).toHaveBeenCalled();
	});

	it('renames a tag by its name alone, with nothing read first', async () => {
		/* A tag has nothing else the rename would have to read back and send unchanged, so the
		   write is the name and nothing is asked before it. */
		mocked.put.mockResolvedValue({ id: 'r1', name: 'shore', asset_count: 2 });
		const { verbs } = wall('tag', [{ id: 'r1', name: 'beach', count: 2 }]);
		verbs.askToRename(['r1']);
		verbs.renameTo = 'shore';
		await verbs.rename();

		expect(mocked.get).not.toHaveBeenCalledWith('/tags/r1');
		expect(mocked.put).toHaveBeenCalledWith('/tags/r1', { body: { name: 'shore' } });
		expect(toasts.items).toHaveLength(0);
	});

	it('names a name that is already taken, for every kind', async () => {
		/* Every kind says so, not only tags; a 409 is the one refusal whose reason a wall showing
		   one spelling cannot convey. */
		mocked.put.mockRejectedValue(new ApiError(409, 'taken'));
		const { verbs } = wall('site', [{ id: 'r1', name: 'QuillMoss' }]);
		verbs.askToRename(['r1']);
		verbs.renameTo = 'Hollowgrain';
		await verbs.rename();
		expect(toasts.items[0].message).toBe('There\'s already a Site called "Hollowgrain"');
	});

	it('stops the box where the route does', () => {
		// A tag's name is 64 on the server and the others 120; one box limit for all five would
		// let a tag name be typed in full and then refused.
		expect(KIND_FACTS.tag.nameLimit).toBe(64);
		for (const kind of ['person', 'site', 'collection', 'photo_set'] as const) {
			expect(KIND_FACTS[kind].nameLimit, kind).toBe(120);
		}
	});

	it('writes nothing for a name that has not changed', async () => {
		const { verbs } = wall('person');
		verbs.askToRename(['r1']);
		await verbs.rename();
		expect(mocked.put).not.toHaveBeenCalled();
	});

	it('says so when the write is refused', async () => {
		mocked.put.mockRejectedValue(new Error('no'));
		const { verbs } = wall('person');
		verbs.askToRename(['r1']);
		verbs.renameTo = 'Rian Fennick';
		await verbs.rename();
		expect(toasts.items[0].message).toBe("That name couldn't be saved");
	});
});

describe('what a delete says it will do', () => {
	it('names the row and what comes off it', () => {
		const { verbs } = wall('person');
		verbs.askToDelete(['r1']);
		expect(verbs.deleteTitle).toBe('Delete "Marla Quist"?');
		expect(verbs.deleteConsequence).toContain('3 files');
		expect(verbs.deleteLabel).toBe('Delete person');
	});

	it('counts the whole selection, because that is the number being decided about', () => {
		const rows: WallRow[] = [
			{ id: 'r1', name: 'beach', count: 4 },
			{ id: 'r2', name: 'shore', count: 6 }
		];
		const { verbs } = wall('tag', rows);
		verbs.askToDelete(['r1', 'r2']);
		expect(verbs.deleteTitle).toBe('Delete 2 tags?');
		expect(verbs.deleteConsequence).toContain('10 files');
		expect(verbs.deleteLabel).toBe('Delete 2');
	});

	it('says it in words where the wall counted in CONTEXT rather than in the library', async () => {
		/*
		 * A wall reached through an entity's tabs counts in context (a person on a tag's People tab
		 * reads the files of hers carrying that tag), while deleting her takes her off every file
		 * she is on. That number would read as reasonable and understate the act by whatever the
		 * filtering was, so a wall with no whole count hands none over and the sentence stops
		 * claiming one.
		 */
		const { verbs } = wall('person', [{ id: 'r1', name: 'Marla Quist' }]);
		verbs.askToDelete(['r1']);
		expect(verbs.deleteConsequence).toContain('every file they are on');
		expect(verbs.deleteConsequence).not.toMatch(/\d/);
	});

	it('will not add up a set where one row-s count is missing', async () => {
		// One missing makes the SUM wrong rather than small, which is the worse of the two.
		const { verbs } = wall('tag', [
			{ id: 'r1', name: 'beach', count: 4 },
			{ id: 'r2', name: 'shore' }
		]);
		verbs.askToDelete(['r1', 'r2']);
		expect(verbs.deleteConsequence).toContain('every file carrying it');
	});

	it('deletes one at a time, so a refusal part-way leaves a readable result', async () => {
		const rows: WallRow[] = [
			{ id: 'r1', name: 'beach', count: 1 },
			{ id: 'r2', name: 'shore', count: 1 }
		];
		const { verbs, changed } = wall('tag', rows);
		verbs.askToDelete(['r1', 'r2']);
		await verbs.remove();
		expect(mocked.del.mock.calls.map((call) => call[0])).toEqual(['/tags/r1', '/tags/r2']);
		expect(changed).toHaveBeenCalled();
	});
});

describe('the opinions, where the kind has them', () => {
	it('turns them ALL on when any of them is not a favorite', async () => {
		/* One target state for the whole set, the way the file grid does it. Toggling each into
		   whatever it was not leaves the set more mixed than it started. */
		const rows: WallRow[] = [
			{ id: 'r1', name: 'Alex Vane', favorite: true },
			{ id: 'r2', name: 'Ines Dray', favorite: false }
		];
		const { verbs } = wall('person', rows);
		await verbs.favoriteAll(['r1', 'r2']);
		expect(mocked.put.mock.calls).toEqual([
			['/people/r1/favorite', { body: { favorite: true } }],
			['/people/r2/favorite', { body: { favorite: true } }]
		]);
	});

	it('writes the stars at the kind-s own address', async () => {
		const { verbs } = wall('photo_set', [{ id: 'r1', name: 'A summer set' }]);
		await verbs.rateAll(['r1'], 4);
		expect(mocked.put).toHaveBeenCalledWith('/photo-sets/r1/rating', { body: { rating: 4 } });
	});

	it('stops at the first refusal rather than saying so once per row', async () => {
		mocked.put.mockRejectedValue(new Error('no'));
		const rows: WallRow[] = [
			{ id: 'r1', name: 'Alex Vane' },
			{ id: 'r2', name: 'Ines Dray' }
		];
		const { verbs } = wall('person', rows);
		await verbs.favoriteAll(['r1', 'r2']);
		expect(mocked.put).toHaveBeenCalledTimes(1);
		expect(toasts.items).toHaveLength(1);
	});
});

describe('the two that go somewhere else', () => {
	it('the chooser opens on the thing-s own page', () => {
		// The chooser needs what Sift already holds for every field and that page's own save, so the
		// menu goes there rather than growing a second copy of the record screen.
		wall('site', [{ id: 'r1', name: 'QuillMoss' }]).verbs.handlers.lookUp?.(['r1']);
		expect(goto).toHaveBeenCalledWith('/sites/r1?enrich=1');
	});

	it('hiding goes to the kind-s own vault route', async () => {
		const { verbs, clear } = wall('collection', [{ id: 'r1', name: 'A shelf' }]);
		await verbs.hideAll(['r1'], true);
		expect(mocked.put).toHaveBeenCalledWith('/collections/r1/vault', { body: { vault: true } });
		expect(clear).toHaveBeenCalled();
	});

	it('hiding rings the library bell, so every other scoped list reads again', async () => {
		// What belongs on every wall just moved. Without the bell, hiding from a wall would leave
		// the other screens holding the row.
		const before = libraryChanges.generation;
		const { verbs } = wall('person', [{ id: 'r1', name: 'Marla Quist' }]);
		await verbs.hideAll(['r1'], true);
		expect(libraryChanges.generation).toBe(before + 1);
	});

	it('rings nothing where nothing moved', async () => {
		mocked.put.mockRejectedValue(new Error('no'));
		const before = libraryChanges.generation;
		const { verbs } = wall('person', [{ id: 'r1', name: 'Marla Quist' }]);
		await verbs.hideAll(['r1'], true);
		expect(libraryChanges.generation).toBe(before);
	});
});

describe('what the sheets are opened on', () => {
	it('sharing names every picked row, and the report names one', () => {
		/* Two questions about the same thing, so they take the same targets: two copies of that
		   mapping is two chances for the panels to disagree about what a row is called. */
		const rows: WallRow[] = [
			{ id: 'r1', name: 'Alex Vane' },
			{ id: 'r2', name: 'Ines Dray' }
		];
		const { verbs } = wall('person', rows);
		verbs.askToShare(['r1', 'r2']);
		expect(verbs.sharing).toEqual([
			{ type: 'person', id: 'r1', label: 'Alex Vane' },
			{ type: 'person', id: 'r2', label: 'Ines Dray' }
		]);
		verbs.reachOf(['r1', 'r2']);
		expect(verbs.reaching).toEqual({ type: 'person', id: 'r1', label: 'Alex Vane' });
	});

	it('the Hidden mark opens the "why is this hidden" panel on that one row', () => {
		const { verbs } = wall('tag', [{ id: 't1', name: 'beach', hidden: true }]);
		verbs.askAboutHidden('t1');
		expect(verbs.hiddenOpen).toBe(true);
		expect(verbs.hiddenAbout).toEqual({ type: 'tag', id: 't1', label: 'beach' });
	});

	it('a share applied or a merge landed lets the picks go and reads the wall again', () => {
		// Both are verbs over a selection, so both let go as well as read again: a share from a
		// tab must not keep its picks.
		const { verbs, changed, clear } = wall('person');
		verbs.applied();
		expect(clear).toHaveBeenCalledTimes(1);
		expect(changed).toHaveBeenCalledTimes(1);
	});

	it('opens nothing at all for ids the wall is no longer holding', () => {
		// A menu can outlive the row it was opened over. Nothing to act on is nothing opened, not a
		// sheet with an empty subject line.
		const { verbs } = wall('person');
		verbs.askToShare(['gone']);
		verbs.askToDelete(['gone']);
		expect(verbs.shareOpen).toBe(false);
		expect(verbs.confirmOpen).toBe(false);
	});
});

describe('what the menu and the bar are told about the pick', () => {
	it('says Unhide only where every picked row is already hidden', () => {
		const rows: WallRow[] = [
			{ id: 'r1', name: 'Alex Vane', hidden: true },
			{ id: 'r2', name: 'Ines Dray', hidden: false },
			{ id: 'r3', name: 'Rian Fennick', hidden: true }
		];
		const { verbs } = wall('person', rows);
		expect(verbs.allHidden(['r1', 'r3'])).toBe(true);
		expect(verbs.allHidden(['r1', 'r2'])).toBe(false);
		// Nothing picked, or a pick the wall is not holding, is not "all hidden".
		expect(verbs.allHidden([])).toBe(false);
		expect(verbs.allHidden(['r1', 'gone'])).toBe(false);
	});

	it('shows the stars the WHOLE pick shares, not the pressed row-s', () => {
		/* Handing over the pressed row's own stars would make a menu over a selection of forty show
		   one of them. */
		const rows: WallRow[] = [
			{ id: 'r1', name: 'A summer set', rating: 4 },
			{ id: 'r2', name: 'A winter set', rating: 4 },
			{ id: 'r3', name: 'A spring set', rating: 2 }
		];
		const { verbs } = wall('photo_set', rows);
		expect(verbs.sharedRating(['r1', 'r2'])).toBe(4);
		expect(verbs.sharedRating(['r1', 'r3'])).toBeNull();
		expect(verbs.sharedRating([])).toBeNull();
	});
});

/*
 * The pin, on the walls whose order it changes. The re-read a pin needs is `changed`, which every
 * wall hands over, so the five entity walls offer Pin as a tag's People tab does.
 */
describe('the pin, where the wall asks for it', () => {
	function pinning(rows: WallRow[]) {
		const changed = vi.fn();
		const clear = vi.fn();
		const verbs = new WallVerbs({
			kind: () => 'site',
			rows: () => rows,
			changed,
			clear,
			pins: true
		});
		return { verbs, changed, clear };
	}

	it('is offered to a wall that asks, and to no other', () => {
		expect(pinning([]).verbs.handlers.pin).toBeDefined();
		// A tab wall hands its own and must not be handed a second.
		expect(wall('site').verbs.handlers.pin).toBeUndefined();
	});

	it('writes the kind-s own pin route for every one, then lets go and reads the wall again', async () => {
		mocked.put.mockResolvedValue({ pinned: true });
		const { verbs, changed, clear } = pinning([
			{ id: 's1', name: 'QuillMoss' },
			{ id: 's2', name: 'Hollowgrain' }
		]);
		await verbs.pinAll(['s1', 's2'], true);
		expect(mocked.put.mock.calls).toEqual([
			['/sites/s1/pin', { body: { pinned: true } }],
			['/sites/s2/pin', { body: { pinned: true } }]
		]);
		expect(clear).toHaveBeenCalledTimes(1);
		expect(changed).toHaveBeenCalledTimes(1);
	});

	it('keeps the pick where the write is refused', async () => {
		mocked.put.mockRejectedValue(new Error('no'));
		const { verbs, clear } = pinning([{ id: 's1', name: 'QuillMoss' }]);
		await verbs.pinAll(['s1'], true);
		expect(clear).not.toHaveBeenCalled();
	});

	it('reads Unpin only where every picked row is already pinned', () => {
		const { verbs } = pinning([
			{ id: 's1', name: 'QuillMoss', pinned: true },
			{ id: 's2', name: 'Hollowgrain', pinned: false }
		]);
		expect(verbs.allPinned(['s1'])).toBe(true);
		expect(verbs.allPinned(['s1', 's2'])).toBe(false);
		expect(verbs.allPinned([])).toBe(false);
	});
});

describe('what the merge sheet is handed', () => {
	it('carries the row picture through, so a candidate card is a face and not a letter', () => {
		/*
		 * A wall reached through a tab (a tag's People tab) must hand the merge sheet pictures, not
		 * only a name and a heart, or every candidate falls back to a coloured letter while the
		 * same sheet off the People wall shows faces.
		 *
		 * Asserted on what reaches `merging`, which is what `EntityWallFlows` maps into the sheet's
		 * `people`; driving the wall would be a test of the selection bar.
		 */
		const face = { src: '/api/faces/tracks/t-9/crop', instead: null };
		const { verbs } = wall('person', [
			{ id: 'p-1', name: 'Marla Quist', picture: face },
			{ id: 'p-2', name: 'Delphine Ostrow' }
		]);

		verbs.askToMerge(['p-1', 'p-2']);

		expect(verbs.merging.map((one) => one.picture)).toEqual([face, undefined]);
	});

	it('hands over every pick, including one this wall is not holding', () => {
		/*
		 * The sheet reads every pick by id, so a pick not on the loaded rows does not fall out, and
		 * two picked stay two; the id is all it needs from here.
		 */
		const { verbs } = wall('person', [{ id: 'p-1', name: 'Marla Quist' }]);

		verbs.askToMerge(['p-1', 'p-far']);

		expect(verbs.mergeOpen).toBe(true);
		expect(verbs.merging.map((one) => one.id)).toEqual(['p-1', 'p-far']);
	});
});

/*
 * The menu's tag picker keeps the selection, and says what landed. A right press keeps the flyout
 * up for the next pick over the same rows, so the selection must not be cleared after the first. A
 * pick the server refused has to say so, or the picker's mark stays on a row nothing was put on.
 */
describe('the tag picker in the menu', () => {
	it('leaves the selection alone, tells the wall, and answers landed', async () => {
		mocked.post.mockResolvedValue(undefined);
		const { verbs, changed, clear } = wall('person', [
			{ id: 'r1', name: 'Marla Quist', count: 3 },
			{ id: 'r2', name: 'Bryn Calloway', count: 1 }
		]);

		const landed = await verbs.tagPick?.pick(['r1', 'r2'], { id: 't1', name: 'Beach' });

		expect(landed).toBe('landed');
		expect(changed).toHaveBeenCalled();
		expect(clear).not.toHaveBeenCalled();
	});

	it('answers refused where nothing went, and partly where some did', async () => {
		mocked.post.mockRejectedValueOnce(new Error('refused'));
		const { verbs } = wall('person', [{ id: 'r1', name: 'Marla Quist', count: 3 }]);
		expect(await verbs.tagPick?.pick(['r1', 'r2'], { id: 't1', name: 'Beach' })).toBe('refused');

		mocked.post.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('refused'));
		expect(await verbs.tagPick?.pick(['r1', 'r2'], { id: 't1', name: 'Beach' })).toBe('partly');
	});
});
