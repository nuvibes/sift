import { vi } from 'vitest';

const listing = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: listing.get }
}));

/* The picks an entity page's tabs gather, as the address carries them, and the Files address they
 * are spent on. Every function here is a string in and a string out, and every fault in them draws
 * perfectly: a pick that drops on a tab change, or a name the parser reads as two. */

import { describe, expect, it } from 'vitest';

import {
	asFilterValue,
	carryPicks,
	clearPicks,
	narrowingOf,
	isPicked,
	pickFieldOf,
	picksOf,
	tabsCarryingPicks,
	togglePick
} from './picks';

const at = (path: string) => new URL(`http://localhost${path}`);

describe('which tabs pick', () => {
	it('names the field each card wall stands for', () => {
		expect(pickFieldOf('people')).toBe('people');
		expect(pickFieldOf('tags')).toBe('tags');
		expect(pickFieldOf('sites')).toBe('sites');
		expect(pickFieldOf('collections')).toBe('collections');
		expect(pickFieldOf('photo_sets')).toBe('photo_sets');
	});

	it('picks nothing on the walls of media, nor on the sites inside a network', () => {
		expect(pickFieldOf('files')).toBeNull();
		expect(pickFieldOf('loops')).toBeNull();
		expect(pickFieldOf('sites_within')).toBeNull();
	});

	/*
	 * A collection's page picks like every other: its Files tab reads the same five fields.
	 * `pickFieldOf` takes no page kind, so the two cases above cover it.
	 */
});

describe('reading and writing the picks', () => {
	it('reads the plain values of the five fields as picks, each once, and nothing else', () => {
		const picks = picksOf(
			at(
				'/people/p1?tags=a%3Ab&people=Jane+Else&people=Jane+Else&rating=5&tags=&people=-Mia&tags=x%7Cy&sites=%22a%2C+b%22'
			)
		);
		expect(picks).toEqual([
			{ field: 'people', name: 'Jane Else' },
			{ field: 'tags', name: 'a:b' },
			{ field: 'sites', name: 'a, b' }
		]);
		expect(isPicked(picks, 'tags', 'a:b')).toBe(true);
		expect(isPicked(picks, 'people', 'Mia')).toBe(false);
	});

	it('adds a pick AS the filter parameter, keeping every other parameter, and takes it off again', () => {
		const once = togglePick(at('/people/p1?show=tags'), { field: 'tags', name: 'beach' });
		expect(once).toBe('/people/p1?show=tags&tags=beach');
		expect(togglePick(at(once), { field: 'tags', name: 'beach' })).toBe('/people/p1?show=tags');
	});

	it('quotes a picked name the parser would split, and reads it back as picked', () => {
		const once = togglePick(at('/people/p1?show=tags'), { field: 'tags', name: 'a, b' });
		expect(new URL(`http://localhost${once}`).searchParams.getAll('tags')).toEqual(['"a, b"']);
		expect(isPicked(picksOf(at(once)), 'tags', 'a, b')).toBe(true);
		expect(togglePick(at(once), { field: 'tags', name: 'a, b' })).toBe('/people/p1?show=tags');
	});

	it('leaves a typed exclusion on the same field alone when a pick comes off', () => {
		expect(
			togglePick(at('/people/p1?show=people&people=-Mia&people=Jane'), {
				field: 'people',
				name: 'Jane'
			})
		).toBe('/people/p1?show=people&people=-Mia');
	});

	it('clears every pick and nothing else', () => {
		expect(clearPicks(at('/tags/t1?show=people&people=Jane&people=-Mia&rating=5'))).toBe(
			'/tags/t1?show=people&people=-Mia&rating=5'
		);
	});
});

describe('moving between tabs', () => {
	const url = at('/people/p1?show=people&people=Jane&tags=-beach');

	it('carries the picks onto every card tab', () => {
		expect(carryPicks('/people/p1?show=tags', url)).toBe(
			'/people/p1?show=tags&people=Jane&tags=-beach'
		);
	});

	it('carries them onto the Files tab too, which they narrow the moment it opens', () => {
		expect(carryPicks('/people/p1', url)).toBe('/people/p1?people=Jane&tags=-beach');
	});

	it('leaves every tab alone when nothing is picked', () => {
		const tabs = [{ id: 'tags', href: '/people/p1?show=tags' }];
		expect(tabsCarryingPicks(tabs, at('/people/p1?show=people&rating=5'))).toEqual(tabs);
	});
});

describe('writing a name as a filter value', () => {
	it('quotes a name the parser would otherwise split or negate', () => {
		expect(asFilterValue('Jane Else')).toBe('Jane Else');
		expect(asFilterValue('a, b')).toBe('"a, b"');
		expect(asFilterValue('this|that')).toBe('"this|that"');
		expect(asFilterValue('-minus')).toBe('"-minus"');
		expect(asFilterValue('say "hi"')).toBe('say hi');
	});
});

describe('the Files count while picks narrow the page', () => {
	it('asks the same listing the Files tab reads, both narrowings joined, one row', async () => {
		/*
		 * The strip shows the scoped count, not the record's whole count, while picks are in force.
		 */
		const { narrowedFilesTotal } = await import('./picks');
		listing.get.mockResolvedValue({ total: 7, items: [] });
		const url = new URL('http://sift.local/people/p1?show=people&people=Jane+Else&tags=beach');

		const total = await narrowedFilesTotal(url, { people: 'Ilva Brennan' });

		expect(total).toBe(7);
		expect(listing.get).toHaveBeenCalledWith('/assets', {
			query: { people: ['Ilva Brennan', 'Jane Else'], tags: 'beach', limit: 1 }
		});
	});

	it("asks nothing when nothing is picked, so the record's own count stands", async () => {
		const { narrowedFilesTotal } = await import('./picks');
		listing.get.mockClear();

		expect(
			await narrowedFilesTotal(new URL('http://sift.local/people/p1?show=people'), {})
		).toBeNull();
		expect(listing.get).not.toHaveBeenCalled();
	});
});

describe("the narrowing a collection's own listing is asked with", () => {
	it('carries every value of the five fields, typed ones included, and nothing else', () => {
		const url = new URL(
			'http://sift.test/collections/c1?show=files&people=Jane+Else&tags=beach&tags=-dusk&sort=newest&rating=4'
		);

		expect(narrowingOf(url)).toEqual({ people: ['Jane Else'], tags: ['beach', '-dusk'] });
	});

	it('is empty when the address narrows nothing', () => {
		expect(narrowingOf(new URL('http://sift.test/collections/c1?show=files'))).toEqual({});
	});
});
