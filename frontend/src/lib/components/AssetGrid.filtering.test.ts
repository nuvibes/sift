/*
 * Filtering INSIDE a screen that already carries a constraint.
 *
 * The filter bar, the facet panel and the presets all filter by writing into the address. Browse
 * reads its whole query from there. The other five screens made of tiles do not: a person's page
 * passes the person and nothing else, because the person is what that page IS.
 *
 * So a grid asking the server for the screen's constraint alone would change the address and draw
 * the chips while the results did not move, on Favorites, Hidden, a person, a site and a
 * collection, and the failure is silent: every part looks like it is working.
 *
 * Asserted on what is SENT rather than on what comes back. What the grid asks the server for is
 * the whole of this behaviour, and it is the one thing a test without a browser can see.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { LOOP_SOURCE } from '$lib/grid/grid.svelte';
import { screenBar } from '$lib/components/shell/screen-bar.svelte';

const asked = vi.hoisted(() => ({
	queries: [] as Record<string, unknown>[],
	paths: [] as string[]
}));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/people/1') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets' || path === '/loops') {
				asked.paths.push(path);
				asked.queries.push(options?.query ?? {});
				return { items: [], total: 0, limit: 50, offset: 0, complete: true };
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		post: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

// `replaceState` as well as `goto`, because the grid writes the tile it is on into the address
// as you scroll. Without it every scroll rejects with an unhandled error, which vitest warns may
// be making tests pass that should not.
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	asked.queries = [];
	asked.paths = [];
	at.url = new URL('http://localhost/people/1');
});

/** Draw the grid the way a screen does, and let its start-up settle. */
async function grid(query: Record<string, string>) {
	host = document.createElement('div');
	document.body.append(host);
	mount(AssetGrid, {
		target: host,
		props: { query, title: 'Files', empty: 'Nothing here' }
	});
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return asked.queries.at(-1) ?? {};
}

describe('a screen that carries its own constraint', () => {
	it('asks for the filters in the address as well as its own constraint', async () => {
		// A person's page, with a tag chosen from the panel. Both have to reach the server, or the
		// chip on the bar describes a filter that never happened.
		at.url = new URL('http://localhost/people/1?q=tags%3Abeach');

		const sent = await grid({ people: 'Jane Doe' });

		expect(sent.people, 'the screen stopped being about this person').toBe('Jane Doe');
		expect(sent.q, 'the filter on the bar never reached the server').toBe('tags:beach');
	});

	it('asks for BOTH people where the address names one as well', async () => {
		// Landing on somebody's page from another person's Seen with tab: the filter is the person
		// you came from, and the wall is the files the two are both on. The screen's own constraint
		// and the address's must both apply, or the chip draws while the request asks for one
		// person.
		at.url = new URL('http://localhost/people/1?people=Jane+Else');

		const sent = await grid({ people: 'Jane Doe' });

		// Repeated rather than joined into one string: the server reads a repeated parameter as
		// "all of these" and hands each value to the parser whole.
		expect(sent.people).toEqual(['Jane Doe', 'Jane Else']);
	});

	it('cannot be pointed at somebody else by the address', async () => {
		// Both constraints apply, so the screen's own person is in every answer: a link can filter
		// this page, not re-aim it.
		at.url = new URL('http://localhost/people/1?people=Jane+Else');

		const sent = await grid({ people: 'Jane Doe' });

		expect(sent.people).toContain('Jane Doe');
	});

	it('keeps an exclusion and a choice whole rather than gluing them to its own value', async () => {
		// A comma would have made `-Jane Else` into a person NAMED "-Jane Else", and `a|b` into
		// (Jane Doe and a) or b. Each side reaches the parser as it was written.
		at.url = new URL('http://localhost/people/1?people=-Jane+Else');

		const sent = await grid({ people: 'Jane Doe' });

		expect(sent.people).toEqual(['Jane Doe', '-Jane Else']);
	});

	it('does not ask twice for a value the screen and the address both carry', async () => {
		// Browse hands the address's own query back down as the screen's, so the two agree by
		// construction there and a wall must not ask for one tag twice.
		at.url = new URL('http://localhost/browse?tags=beach');

		const sent = await grid({ tags: 'beach' });

		expect(sent.tags).toBe('beach');
	});

	it('draws every value of a name the address gives more than once', async () => {
		// The bar draws a chip per pair, so two chips have to be two constraints. Read as an object
		// only the last would survive: a chip in force on the bar and nowhere else.
		at.url = new URL('http://localhost/browse?tags=beach&tags=sunset');

		const sent = await grid({});

		expect(sent.tags).toEqual(['beach', 'sunset']);
	});

	it('still lets the screen win a field a file has only one of', async () => {
		// A rating is one value per file, so two at once is nothing at all. Those keep the rule they
		// have always had.
		at.url = new URL('http://localhost/favorites?fav=no');

		const sent = await grid({ fav: 'yes' });

		expect(sent.fav).toBe('yes');
	});

	it('takes only the query language out of the address, never the rest of it', async () => {
		// The order and the page are in the address too, and neither is a filter. Handed to the
		// engine as one they would filter to nothing, because no asset has them.
		at.url = new URL('http://localhost/people/1?sort=oldest&offset=120&nonsense=x');

		const sent = await grid({ people: 'Jane Doe' });

		// The grid sends an offset of its own (it pages) so the check is that the address's did
		// not become it. Page one of a screen somebody just filtered, not page three of the last one.
		expect(sent.offset, 'the address decided which page this is').toBe(0);
		expect(sent.nonsense).toBeUndefined();
		// Sort rides alongside as its own parameter, which is why it is here rather than absent.
		expect(sent.sort).toBe('oldest');
	});

	it('orders a wall of files similar to one file by Similarity unless one was picked', async () => {
		// Sent as newest, the server had no choice but newest: a named order wins there.
		at.url = new URL('http://localhost/browse?like=01HX0000000000000000000007');

		const sent = await grid({ like: '01HX0000000000000000000007' });

		expect(sent.sort).toBe('similarity');
		const sorts = screenBar.tools.sorts;
		const offered = typeof sorts === 'string' ? [] : (sorts?.map((option) => option.value) ?? []);
		expect(offered).toContain('similarity');
		// Closest match ranks words, and a wall of lookalikes has none to rank.
		expect(offered).not.toContain('relevance');
	});

	it('still works for a screen whose query IS the address', async () => {
		// Browse passes what it read from the address, so merging must be a no-op rather than a
		// second copy of every parameter.
		at.url = new URL('http://localhost/browse?q=tags%3Abeach');

		const sent = await grid({ q: 'tags:beach' });

		expect(sent.q).toBe('tags:beach');
	});
});

describe("a wall of marks takes the bar's filters", () => {
	/* A wall of marks still offers the panel. A mark is a piece of a file, so
	   the file's facets are what a filter on it means: the address's filters go to `/loops` beside
	   the page's own filter, and the bar is told the wall in the FILE language (the files that
	   have a mark, and the page by name) so its columns count those files and not the library. */
	async function marks(query: Record<string, string>, filesQuery?: Record<string, string>) {
		host = document.createElement('div');
		document.body.append(host);
		mount(AssetGrid, {
			target: host,
			props: { query, filesQuery, source: LOOP_SOURCE, title: 'Loops', empty: 'None' }
		});
		flushSync();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		return asked.queries.at(-1) ?? {};
	}

	it("asks the marks' route with the address's filters and the page's own narrowing", async () => {
		at.url = new URL('http://localhost/people/1?show=loops&tags=beach');

		const sent = await marks({ person: 'p1' }, { people: 'Jane Doe' });

		expect(asked.paths.at(-1)).toBe('/loops');
		expect(sent.person, 'the tab stopped being about this person').toBe('p1');
		expect(sent.tags, 'the chip on the bar never reached the wall').toBe('beach');
	});

	it('tells the bar the wall in the words it counts in, and offers it the panel', async () => {
		at.url = new URL('http://localhost/people/1?show=loops');

		await marks({ person: 'p1' }, { people: 'Jane Doe' });

		expect(screenBar.tools.filterable).toBe(true);
		expect(screenBar.tools.query).toEqual({ loops: 'any', people: 'Jane Doe' });
	});

	it('counts the files that have a mark on the plain Loops wall', async () => {
		at.url = new URL('http://localhost/loops');

		await marks({});

		expect(screenBar.tools.query).toEqual({ loops: 'any' });
	});

	it('tells the bar the Loops column is one row here, so the panel leaves it out', async () => {
		/* Every file on a wall of moments has a Loop: the column would say only that. Browse and
		   Theater are told nothing of the kind, so they offer every column. */
		at.url = new URL('http://localhost/loops');

		await marks({});

		expect(screenBar.tools.fixed).toEqual(['loops']);
	});
});
