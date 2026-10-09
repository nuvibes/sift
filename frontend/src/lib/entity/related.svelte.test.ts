/* The tab set six entity pages share, and the filtering each tab sends: addresses and query
 * parameters, which draw perfectly when wrong and answer the wrong question. */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	kindOf,
	loadRelated,
	narrowingFor,
	ordersFor,
	tabSort,
	iconOf,
	pageOf,
	relatedHref,
	showing,
	TabCounts,
	tabsFor,
	type EntityKind,
	type RelatedKind
} from '$lib/entity/related.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

const KINDS: EntityKind[] = ['person', 'tag', 'site', 'collection', 'photo_set', 'song'];

/** What a wall answers with. Every related wall answers this shape (see the module's own note). */
function page(items: { id: string; name: string }[] = [], total = items.length) {
	return { items, total, limit: 60, offset: 0 };
}

beforeEach(() => {
	vi.resetAllMocks();
	mocked.get.mockResolvedValue(page());
});

describe('what each page can show', () => {
	it('never offers a page a tab for its own kind', () => {
		// The one that reads as a page linking to itself.
		const own: Record<EntityKind, RelatedKind> = {
			person: 'people',
			tag: 'tags',
			// The wall a Site is a ROW of, which is not the `sites_within` tab its own page carries:
			// that one is what is PART OF it, and is a different question about the same wall.
			site: 'sites',
			collection: 'collections',
			photo_set: 'photo_sets',
			song: 'songs'
		};
		for (const on of KINDS) {
			const ids = tabsFor(on, 'x', '/x').map((tab) => tab.id);
			if (on === 'person') expect(ids).toContain('people');
			else expect(ids).not.toContain(own[on]);
		}
	});

	it('shows Files first on every page, because that is what somebody came to the page for', () => {
		for (const on of KINDS) {
			expect(tabsFor(on, 'x', '/x')[0].id).toBe('files');
		}
	});

	it('gives a site a Sites tab, and gives nothing else one', () => {
		/* A site is the only thing here that can be part of another of its own kind. */
		expect(tabsFor('site', 'pf1', '/sites/pf1').map((tab) => tab.id)).toContain('sites_within');
		for (const on of KINDS.filter((kind) => kind !== 'site')) {
			expect(tabsFor(on, 'x', '/x').map((tab) => tab.id)).not.toContain('sites_within');
		}

		const sites = tabsFor('site', 'pf1', '/sites/pf1').find((tab) => tab.id === 'sites_within');
		expect(sites?.label).toBe('Sites');
		expect(sites?.href).toBe('/sites/pf1?show=sites_within');
	});

	it("gives a person's people wall its own glyph, because it is not a person", () => {
		/* The single figure names a person, which is the page you are already on. */
		const own = tabsFor('person', 'p1', '/people/p1').find((tab) => tab.id === 'people');
		expect(own?.label).toBe('Seen with');
		expect(own?.icon).toBe('diversity_3');

		const elsewhere = tabsFor('tag', 't1', '/tags/t1').find((tab) => tab.id === 'people');
		expect(elsewhere?.label).toBe('People');
		expect(elsewhere?.icon).toBe('person');
	});

	it('puts a Music tab right after Collections, and last on a Collection', () => {
		/* One agreed place on every page, so the strip reads the same way everywhere. */
		const ids = (on: EntityKind) => tabsFor(on, 'x', '/x').map((tab) => tab.id);
		for (const on of ['person', 'tag', 'site'] as const) {
			const at = ids(on).indexOf('songs');
			expect(at).toBe(ids(on).indexOf('collections') + 1);
		}
		expect(ids('collection').at(-1)).toBe('songs');
		expect(ids('photo_set')).not.toContain('songs');
		expect(tabsFor('tag', 't1', '/tags/t1').find((tab) => tab.id === 'songs')?.label).toBe('Music');
	});

	it("gives a song's page every tab but Photo Sets, in the other pages' order", () => {
		expect(tabsFor('song', 'so1', '/songs/so1').map((tab) => tab.id)).toEqual([
			'files',
			'loops',
			'people',
			'tags',
			'sites',
			'collections'
		]);
	});

	it('leaves the plain page plain and puts every other tab in the address', () => {
		const tabs = tabsFor('person', 'p1', '/people/p1');
		expect(tabs.find((tab) => tab.id === 'files')?.href).toBe('/people/p1');
		expect(tabs.find((tab) => tab.id === 'tags')?.href).toBe('/people/p1?show=tags');
	});

	it('calls a person their own wall of people Seen with, and a set of pictures Pictures', () => {
		const seen = tabsFor('person', 'p1', '/people/p1').find((tab) => tab.id === 'people');
		expect(seen?.label).toBe('Seen with');

		const pictures = tabsFor('photo_set', 's1', '/photo-sets/s1').find((tab) => tab.id === 'files');
		expect(pictures?.label).toBe('Pictures');
		// The same wall on any other page is still Files: the word is the page's, not the wall's.
		expect(tabsFor('tag', 't1', '/tags/t1').find((tab) => tab.id === 'files')?.label).toBe('Files');
	});

	it('carries a count only where one was given, so a wall that has not answered shows no number', () => {
		// `0` and "not yet known" are different facts, and a zero that becomes an eight a moment
		// later reports a fault that is not there.
		const tabs = tabsFor('person', 'p1', '/people/p1', { tags: 0 });
		expect(tabs.find((tab) => tab.id === 'tags')?.count).toBe(0);
		expect(tabs.find((tab) => tab.id === 'people')?.count).toBeUndefined();
	});
});

describe("where one thing's own page is", () => {
	it('spells every kind the way its wall is spelt, not the way the kind is', () => {
		// The one that matters is the photo set: it is named `photo_set` and filed at
		// `/photo-sets`, so anything building the address from the kind by hand gets it wrong in a
		// way that 404s only for that one kind.
		expect(pageOf('person', 'p-1')).toBe('/people/p-1');
		expect(pageOf('tag', 't-1')).toBe('/tags/t-1');
		expect(pageOf('site', 's-1')).toBe('/sites/s-1');
		expect(pageOf('collection', 'c-1')).toBe('/collections/c-1');
		expect(pageOf('photo_set', 'ps-1')).toBe('/photo-sets/ps-1');
	});

	it('gives every kind a page, so nothing falls through the wall it is a row of', () => {
		for (const kind of KINDS) expect(pageOf(kind, 'x')).toMatch(/^\/[a-z-]+\/x$/);
	});
});

describe('which tab an address names', () => {
	it('falls back to Files when the address names none', () => {
		expect(showing('person', null)).toBe('files');
	});

	it('falls back to Files when the address names a tab this page does not have', () => {
		// A link built for a person's page and opened on a collection's.
		expect(showing('collection', 'photo_sets')).toBe('files');
		expect(showing('person', 'nonsense')).toBe('files');
	});

	it('keeps a tab the page really has', () => {
		expect(showing('person', 'loops')).toBe('loops');
	});
});

/* WHERE A CARD LEADS, which is the other half of the one table the counts are read off. */
describe('where a card leads', () => {
	it('carries the page it was pressed from, as a filter the bar can draw', () => {
		expect(relatedHref('person', 'people', 'p2', 'Jane Doe')).toBe('/people/p2?people=Jane+Doe');
		expect(relatedHref('tag', 'people', 'p2', 'beach')).toBe('/people/p2?tags=beach');
		expect(relatedHref('person', 'photo_sets', 'ps3', 'Jane Doe')).toBe(
			'/photo-sets/ps3?people=Jane+Doe'
		);
		// A song on a tag's Music tab opens its page filtered to that tag's files.
		expect(relatedHref('tag', 'songs', 'so1', 'beach')).toBe('/songs/so1?tags=beach');
	});

	it('leaves the two plain walls plain', () => {
		// A site inside a network is reached by a column rather than by files; a collection's wall
		// takes no query at all, so a filter sent there is a chip nothing could act on.
		expect(relatedHref('site', 'sites_within', 's4', 'Another Studio')).toBe('/sites/s4');
		expect(relatedHref('person', 'collections', 'c5', 'Jane Doe')).toBe('/collections/c5');
	});

	it('carries nothing while the page has not said its name yet', () => {
		// An address with an empty filter on it draws a chip for nobody.
		expect(relatedHref('person', 'people', 'p2', '')).toBe('/people/p2');
		expect(relatedHref('person', 'people', 'p2')).toBe('/people/p2');
	});

	it('refuses a wall of media, which has no card to press', () => {
		// Files and loops are drawn by the media grid and a tile opens a file at a moment, so there
		// is no page to lead to.
		expect(() => relatedHref('person', 'loops', 'l1')).toThrow();
		expect(() => relatedHref('person', 'files', 'a1')).toThrow();
	});

	/* THE PROPERTY THE WHOLE ARRANGEMENT EXISTS FOR, asked of every wall rather than of one: a
	 * card asks for the filtered count exactly when its press carries the filtering. */
	it('counts the narrowed tally on exactly the walls whose press carries the page', async () => {
		const walls: Exclude<RelatedKind, 'files' | 'loops'>[] = [
			'photo_sets',
			'tags',
			'people',
			'sites',
			'sites_within',
			'collections',
			'songs'
		];
		for (const wall of walls) {
			mocked.get.mockClear();
			const on = wall === 'sites_within' ? 'site' : 'person';
			await loadRelated(on, 'x1', wall);
			const asked = mocked.get.mock.calls[0][1]?.query ?? {};
			const carried = relatedHref(on, wall, 'r1', 'Jane Doe');
			expect(`${wall}: ${'count' in asked}`).toBe(`${wall}: ${carried.includes('?')}`);
		}
	});
});

describe('the narrowing a wall is asked for', () => {
	it("asks a person's own people wall for who ELSE turns up", async () => {
		await loadRelated('person', 'p1', 'people');

		const [path, options] = mocked.get.mock.calls[0];
		expect(path).toBe('/people');
		// `person` here would answer "the people on this person's files", which includes them.
		expect(options?.query).toMatchObject({ with_person: 'p1' });
		expect(options?.query).not.toHaveProperty('person');
	});

	it("narrows a site's Sites wall by `parent`, because it is not about files at all", async () => {
		await loadRelated('site', 'pf1', 'sites_within');

		const [path, options] = mocked.get.mock.calls[0];
		// The same wall the Sites tab reads, asked the other question.
		expect(path).toBe('/sites');
		expect(options?.query).toMatchObject({ parent: 'pf1' });
		expect(options?.query).not.toHaveProperty('site');
	});

	it('narrows every other wall by the page it is on', async () => {
		await loadRelated('person', 'p1', 'tags');
		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ person: 'p1' });

		await loadRelated('tag', 't1', 'people');
		expect(mocked.get.mock.calls[1][0]).toBe('/people');
		expect(mocked.get.mock.calls[1][1]?.query).toMatchObject({ tag: 't1' });

		await loadRelated('photo_set', 's1', 'sites');
		expect(mocked.get.mock.calls[2][0]).toBe('/sites');
		expect(mocked.get.mock.calls[2][1]?.query).toMatchObject({ photo_set: 's1' });
	});

	/* ONE TABLE, TWO READERS: where a card leads and what it counts. */
	it('asks for the narrowed tally exactly where the press carries the page', async () => {
		await loadRelated('person', 'p1', 'people');
		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ count: 'narrowed' });

		await loadRelated('tag', 't1', 'sites');
		expect(mocked.get.mock.calls[1][1]?.query).toMatchObject({ count: 'narrowed' });

		await loadRelated('person', 'p1', 'photo_sets');
		expect(mocked.get.mock.calls[2][1]?.query).toMatchObject({ count: 'narrowed' });
	});

	it('asks for nothing of the kind where the press is plain', async () => {
		// A collection's wall takes no query and a site inside a network is reached by a column, so
		// both open whole, and a card that asked for the filtered count there would print a number
		// smaller than the page it opens.
		await loadRelated('person', 'p1', 'collections');
		expect(mocked.get.mock.calls[0][1]?.query).not.toHaveProperty('count');

		await loadRelated('site', 'pf1', 'sites_within');
		expect(mocked.get.mock.calls[1][1]?.query).not.toHaveProperty('count');
	});

	it("asks a song's marks by the query language's `songs`, which the marks wall reads", () => {
		/* `/loops` declares no `song` parameter, and an undeclared one is ignored: the tab would
		   draw every mark in the library under the song's name. */
		const asked = Object.fromEntries(narrowingFor('song', 'so1', 'loops'));
		expect(asked).toEqual({ songs: 'so1' });
		// Every other wall on a song's page keeps the walls' own `song`.
		expect(Object.fromEntries(narrowingFor('song', 'so1', 'collections'))).toEqual({ song: 'so1' });
	});

	it('asks for the page it was told to, so a wall past the first row is reachable', async () => {
		await loadRelated('tag', 't1', 'loops', { limit: 20, offset: 40 });

		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ limit: 20, offset: 40 });
	});

	it('refuses Files, which the media grid fetches for itself', async () => {
		// Asking here would build an address for a wall that does not exist.
		await expect(
			loadRelated('person', 'p1', 'files' as Exclude<RelatedKind, 'files'>)
		).rejects.toThrow();
		expect(mocked.get).not.toHaveBeenCalled();
	});

	it('reads the count out of the same answer the cards came from', async () => {
		mocked.get.mockResolvedValue(page([{ id: 'a', name: 'beach' }], 12));

		const answered = await loadRelated('person', 'p1', 'tags');

		expect(answered.items).toHaveLength(1);
		expect(answered.total).toBe(12);
	});

	it('reads an answer missing its halves as empty rather than throwing', async () => {
		mocked.get.mockResolvedValue({} as unknown as ReturnType<typeof page>);

		const answered = await loadRelated('person', 'p1', 'tags');

		expect(answered.items).toEqual([]);
		expect(answered.total).toBe(0);
	});
});

describe('a tab searched and ordered', () => {
	it('asks the words anywhere in the name, and nothing of the kind with none', async () => {
		await loadRelated('person', 'p1', 'tags', { words: 'bea' });
		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ prefix: 'bea', anywhere: 'true' });

		await loadRelated('person', 'p1', 'tags');
		expect(mocked.get.mock.calls[1][1]?.query).not.toHaveProperty('prefix');
		expect(mocked.get.mock.calls[1][1]?.query).not.toHaveProperty('anywhere');
	});

	it('asks for the order chosen', async () => {
		await loadRelated('person', 'p1', 'sites', { sort: 'name_az' });
		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ sort: 'name_az' });
	});

	it("offers a tab its wall's orders, and a Music tab the order by artist besides", () => {
		const tags = ordersFor('tags').map((one) => one.value);
		expect(tags).toEqual(expect.arrayContaining(['largest', 'largest_total', 'name_az', 'rating']));
		expect(tags).not.toContain('artist');
		expect(ordersFor('songs').map((one) => one.value)).toContain('artist');
	});

	it('remembers one order per wall, refusing one the wall does not offer', () => {
		expect(tabSort('tags').value).toBe('largest');
		tabSort('tags').set('name_za');
		expect(tabSort('tags_within').value).toBe('name_za');
		expect(tabSort('people').value).toBe('largest');
		tabSort('people').set('artist');
		expect(tabSort('people').value).toBe('largest');
	});
});

describe('a count that has not moved', () => {
	/* The guard that keeps a page from freezing. The Loops tab draws `AssetGrid`, which reports
	 * its total from an EFFECT rather than once per fetch. */

	it('does not replace the object it holds', () => {
		const counts = new TabCounts();
		counts.saw('loops', 7);
		const first = counts.current;
		counts.saw('loops', 7);

		expect(counts.current).toBe(first);
	});

	it('still replaces it when the number really moved', () => {
		const counts = new TabCounts();
		counts.saw('loops', 7);
		const first = counts.current;
		counts.saw('loops', 8);

		expect(counts.current).not.toBe(first);
		expect(counts.current.loops).toBe(8);
	});

	it('carries the mark beside a tab as well as the number on it', () => {
		/* `disagreements` is not a tab and rides on the same map, because it arrives on the same
		 * request: every number on one strip is taken at one moment. */
		const counts = new TabCounts();
		counts.saw('disagreements', 3);
		expect(counts.current.disagreements).toBe(3);

		counts.saw('disagreements', 0);
		expect(counts.current.disagreements).toBe(0);
	});

	it('takes the first answer for a tab that had none', () => {
		// `undefined === 0` is false, so a genuine zero is written rather than swallowed, which
		// matters, because "no answer yet" and "none of them" draw differently.
		const counts = new TabCounts();
		counts.saw('tags', 0);

		expect(counts.current.tags).toBe(0);
	});
});

describe('the strip after the library moves', () => {
	/* An act on the page (a song's artists edited, a share, a tag put on) writes a History line
	 * and rings the library's bell. */

	it('asks again for the page it follows, and the later answer wins', async () => {
		let lines = 3;
		mocked.get.mockImplementation(async (path: string) =>
			path === '/songs/s1/history' ? Array(lines).fill({}) : { files: 1 }
		);
		const counts = new TabCounts();
		counts.follow('song', 's1');
		await vi.waitFor(() => expect(counts.current.history).toBe(3));
		expect(mocked.get).toHaveBeenCalledWith('/related/song/s1');

		lines = 4;
		counts.refresh();
		await vi.waitFor(() => expect(counts.current.history).toBe(4));
	});

	it('leaves History without a number when its thread cannot be read', async () => {
		/* A subject this user may not see answers no thread: no number, as for a tab not there. */
		mocked.get.mockImplementation(async (path: string) => {
			if (path === '/people/p1/history') throw new Error('not found');
			return { files: 1 };
		});
		const counts = new TabCounts();
		counts.follow('person', 'p1');
		await vi.waitFor(() => expect(counts.current.files).toBe(1));
		await Promise.resolve();
		expect(counts.current.history).toBeUndefined();
	});

	it('names the boxes the History mark is about, from the same answer', async () => {
		mocked.get.mockResolvedValueOnce({
			files: 1,
			disagreements: 2,
			disagreement_boxes: ['StashDB']
		});
		const counts = new TabCounts();
		counts.follow('person', 'p1');
		await vi.waitFor(() => expect(counts.current.disagreements).toBe(2));
		expect(counts.boxes).toEqual(['StashDB']);
		expect(counts.current).not.toHaveProperty('disagreement_boxes');

		// The same names again are not a change, so the strip is not re-derived for nothing.
		const first = counts.boxes;
		counts.sawBoxes(['StashDB']);
		expect(counts.boxes).toBe(first);
		counts.sawBoxes([]);
		expect(counts.boxes).toEqual([]);

		// The panel under History reports both together, and its answer is the fresher one.
		counts.sawDisagreements(1, ['FansDB']);
		expect(counts.current.disagreements).toBe(1);
		expect(counts.boxes).toEqual(['FansDB']);
	});

	it('is rung by the library on every page that holds a strip', () => {
		const routes = join(__dirname, '..', '..', 'routes');
		for (const kind of ['collections', 'people', 'photo-sets', 'sites', 'songs', 'tags']) {
			const text = readFileSync(join(routes, kind, '[id]', '+page.svelte'), 'utf8');
			expect(text, `${kind} holds a strip`).toContain('counts.follow(');
			expect(text, `${kind} re-reads it`).toContain(
				'reloadOnLibraryChange(() => counts.refresh())'
			);
		}
	});
});

describe('the mark one kind of entity wears', () => {
	it('is the same one the rail draws for that kind of wall', () => {
		// Read off `SPECS` rather than from a second table, so a page's header and the row somebody
		// pressed to get there cannot show two different glyphs for one destination.
		expect(iconOf('tag')).toBe('shoppingmode');
		expect(iconOf('site')).toBe('public');
		expect(iconOf('collection')).toBe('box');
		expect(iconOf('photo_set')).toBe('photo_library');
	});

	it('gives a person the single figure and not the seen-with group', () => {
		// `iconFor` answers a different question (what a wall of OTHER things is called on the page
		// showing it), and its one exception is exactly wrong here: a person's page is about one
		// person, so the group of three would be a page saying it is about several.
		expect(iconOf('person')).toBe('person');
	});

	it('answers for every kind a page can be about', () => {
		for (const kind of KINDS) expect(iconOf(kind)).toBeTruthy();
	});
});

describe('what kind of thing a wall-s rows are', () => {
	it('answers with the kind whose wall it is', () => {
		// The inverse of one table rather than a second table of the same fact.
		expect(kindOf('people')).toBe('person');
		expect(kindOf('tags')).toBe('tag');
		expect(kindOf('sites')).toBe('site');
		expect(kindOf('collections')).toBe('collection');
		expect(kindOf('photo_sets')).toBe('photo_set');
	});

	it('answers Site for the wall that is the sites wall asked another question', () => {
		/* `sites_within` is what is PART OF a Site rather than a wall of its own, and a row of
		   it is a Site, so it is not in the table being inverted and is written out beside it. */
		expect(kindOf('sites_within')).toBe('site');
	});

	it('answers nothing for the two walls that are not named things', () => {
		// The same absence `pinnableOf` reports for them, and for the same reason: a file and a
		// moment are media, and none of these verbs is about either.
		expect(kindOf('files')).toBeNull();
		expect(kindOf('loops')).toBeNull();
	});
});

describe('the size beside the Files number', () => {
	it('goes when a wall reports a Files number that moved, and stays when it did not', () => {
		const strip = new TabCounts();
		strip.current = { files: 10, files_bytes: 500 };
		strip.saw('files', 10);
		expect(strip.current.files_bytes).toBe(500);
		strip.saw('files', 11);
		expect(strip.current).toEqual({ files: 11 });
	});
});
