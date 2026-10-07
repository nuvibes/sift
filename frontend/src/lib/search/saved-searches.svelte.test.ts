/* The searches somebody chose to keep, and the three things done to them.
 *
 * Each of them calls the server and then reconciles the local copy, and what is worth holding is
 * WHICH way round. Saving reloads rather than appending, because the server upserts on the name,
 * so saving over a name already used would otherwise show as a second row until the next fetch.
 * Renaming and removing are optimistic, because a label moving and a row leaving are both things
 * an undo would look wrong on, and the rename puts the list back itself when the server refuses,
 * which the remove deliberately does not.
 */

import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, patch: mocks.patch, del: mocks.del }
}));

import { libraryChanges, mine } from '$lib/library/changes.svelte';

import { asAQuestion, goneLabel, keptValueLabel, savedSearches } from './saved-searches.svelte';

const BEACH = { id: 's-1', name: 'Beach', query: 'tags=beach&type=video' };
const DOGS = { id: 's-2', name: 'Dogs', query: 'tags=dog' };

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue({ items: [] });
	mocks.post.mockResolvedValue({});
	mocks.patch.mockResolvedValue({});
	mocks.del.mockResolvedValue({});
	// A module singleton, so each test starts it from a known state rather than from the last one.
	savedSearches.items = [];
	savedSearches.loaded = false;
	savedSearches.busy = false;
});

it('saves a question, not a place in the answer', () => {
	/* A saved search carrying the grid's scroll anchor would re-run at row 57 of 58. */
	expect(asAQuestion('tags=beach&from=01HX0&offset=57&type=video')).toBe('tags=beach&type=video');
	expect(asAQuestion('tags=beach')).toBe('tags=beach');
	expect(asAQuestion('')).toBe('');
});

it('strips the position off before the query is sent to be kept', async () => {
	mocks.post.mockResolvedValue(undefined);
	mocks.get.mockResolvedValue({ items: [] });

	await savedSearches.save('Beach', 'tags=beach&from=01HX0&offset=57');

	expect(mocks.post).toHaveBeenCalledWith('/search/saved', {
		body: { name: 'Beach', kind: 'asset', query: 'tags=beach' }
	});
});

it('asks the server once however many times the modal is opened', async () => {
	// It loads lazily, so a session that never opens the Filters modal never fetches it, and one
	// that opens it forty times still asks once.
	mocks.get.mockResolvedValue({ items: [BEACH] });

	await savedSearches.ensure();
	await savedSearches.ensure();

	expect(mocks.get).toHaveBeenCalledTimes(1);
	expect(savedSearches.items).toEqual([BEACH]);
	expect(savedSearches.loaded).toBe(true);
});

it('does not ask a second time while the first answer is still coming', async () => {
	let settle!: (value: { items: (typeof BEACH)[] }) => void;
	mocks.get.mockReturnValueOnce(new Promise((resolve) => (settle = resolve)));

	const first = savedSearches.ensure();
	const second = savedSearches.ensure();
	settle({ items: [BEACH] });
	await Promise.all([first, second]);

	expect(mocks.get).toHaveBeenCalledTimes(1);
});

it('stops saying it is busy even when the read fails', async () => {
	// Left true, every later `ensure` returns immediately and the list is empty for the rest of the
	// session: a screen that has quietly stopped asking.
	mocks.get.mockRejectedValue(new Error('no'));

	await expect(savedSearches.reload()).rejects.toThrow();

	expect(savedSearches.busy).toBe(false);
	expect(savedSearches.loaded).toBe(false);
});

it('reloads after a save rather than adding a row of its own', async () => {
	// The server upserts on the NAME. Appending would show a replaced search as two rows until
	// something else fetched the list.
	mocks.get.mockResolvedValue({ items: [BEACH] });

	await savedSearches.save('Beach', 'tags=beach&type=video');

	expect(mocks.post).toHaveBeenCalledWith('/search/saved', {
		body: { name: 'Beach', kind: 'asset', query: 'tags=beach&type=video' }
	});
	expect(savedSearches.items).toEqual([BEACH]);
});

it('moves a label immediately, keeping the query it points at', async () => {
	mocks.get.mockResolvedValue({ items: [BEACH, DOGS] });
	await savedSearches.ensure();

	await savedSearches.rename('s-1', 'Seaside');

	expect(mocks.patch).toHaveBeenCalledWith('/search/saved/s-1', { body: { name: 'Seaside' } });
	expect(savedSearches.items).toEqual([{ ...BEACH, name: 'Seaside' }, DOGS]);
});

it('puts the old name back when the rename is refused, and says so', async () => {
	mocks.get.mockResolvedValue({ items: [BEACH, DOGS] });
	await savedSearches.ensure();
	mocks.patch.mockRejectedValue(new Error('no'));

	await expect(savedSearches.rename('s-1', 'Seaside')).rejects.toThrow();

	expect(savedSearches.items).toEqual([BEACH, DOGS]);
});

it('takes a row out immediately and tells the server after', async () => {
	mocks.get.mockResolvedValue({ items: [BEACH, DOGS] });
	await savedSearches.ensure();

	await savedSearches.remove('s-1');

	expect(savedSearches.items).toEqual([DOGS]);
	expect(mocks.del).toHaveBeenCalledWith('/search/saved/s-1');
});

/* And the fourth thing done to them, which nothing on this screen does: somebody else moves them.
 *
 * A saved search is this account's own, so it can be written from a second browser or from the
 * desktop shell, and the live connection says so by ringing the bell for a list of this account's
 * own. This store is not a component and cannot watch a rune (it lives as long as the tab), so
 * it is subscribed at module scope instead, and a subscription made once at import is exactly the
 * kind that goes on looking correct after it has stopped being wired to anything.
 */
it('brings the list up to date when the same account writes one somewhere else', async () => {
	mocks.get.mockResolvedValue({ items: [BEACH] });
	await savedSearches.ensure();
	mocks.get.mockResolvedValue({ items: [BEACH, DOGS] });

	mine.changed();
	await vi.waitFor(() => expect(savedSearches.items).toEqual([BEACH, DOGS]));

	expect(mocks.get).toHaveBeenCalledTimes(2);
});

it('asks for nothing when the list has never been opened', async () => {
	/* The half that is easy to leave out, and it is the one that costs: the store loads lazily, so a
	 * session that never opens the Filters modal has nothing to bring up to date. Reloading anyway
	 * would put a request behind every announcement, on behalf of a screen nobody has looked at,
	 * and a scan rings these bells for as long as it runs. */
	expect(savedSearches.loaded).toBe(false);

	mine.changed();
	await Promise.resolve();

	expect(mocks.get).not.toHaveBeenCalled();
});

/* A kept filter names things by id and reads back under today's names (the server swaps them in).
 * What the address cannot say for itself arrives as a note, and the chip reads the note: a thing
 * deleted since, a name nothing answers to any more, an id whose name two things share. */
const GONE_ID = '01J5T6R7S8XYZ3ABCDEFGH4JK5';

it('keeps the words for what the address cannot say, and forgets them on the next load', async () => {
	mocks.get.mockResolvedValue({
		items: [
			{
				...BEACH,
				query: `tags=${GONE_ID}|harbour&people=01M1DWM88BX1MPCXPAXEQRVCWY`,
				named: [
					{ field: 'tags', value: GONE_ID, name: null },
					{ field: 'tags', value: 'harbour', name: null },
					{ field: 'people', value: '01M1DWM88BX1MPCXPAXEQRVCWY', name: 'Ada Twin' }
				]
			}
		]
	});
	await savedSearches.ensure();

	expect(keptValueLabel('tags', GONE_ID)).toBe('a tag that no longer exists');
	expect(keptValueLabel('tags', 'harbour')).toBe('a tag named harbour no longer exists');
	expect(keptValueLabel('people', '01M1DWM88BX1MPCXPAXEQRVCWY')).toBe('Ada Twin');

	mocks.get.mockResolvedValue({ items: [BEACH] });
	await savedSearches.reload();
	expect(keptValueLabel('tags', GONE_ID)).toBeUndefined();
});

it("names a gone thing by its kind, in the screen's words", () => {
	expect(goneLabel('platforms', GONE_ID)).toBe('a Site that no longer exists');
	expect(goneLabel('people', GONE_ID)).toBe('a person who no longer exists');
	expect(goneLabel('photo_sets', 'Pier')).toBe('a Photo Set named Pier no longer exists');
});

/* The row's own fault: a tag renamed on another screen while the list is loaded. The server rings
 * the library bell for a rename, and the list is read again, so the chips say the new name. */
it('reads the list again when something in the library is renamed', async () => {
	mocks.get.mockResolvedValue({ items: [{ ...BEACH, query: 'tags=harbour' }] });
	await savedSearches.ensure();
	mocks.get.mockResolvedValue({ items: [{ ...BEACH, query: 'tags=quayside' }] });

	libraryChanges.changed();
	await vi.waitFor(() => expect(savedSearches.items[0]?.query).toBe('tags=quayside'));
});
