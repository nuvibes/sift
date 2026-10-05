/*
 * THE ORDERS A WALL OFFERS, and what it calls them.
 *
 * Favorites offers two orders no other wall of files does (when each heart was pressed, either way
 * round), and the wall of marks names its time orders by what they read (when each mark was made).
 * Asserted on the menu the bar is handed and on the order a page is asked in, which is the whole of
 * both behaviours a test without a browser can see. That the server obeys the order is pinned where
 * the statement lives.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { ordersOffered, screenBar } from '$lib/components/shell/screen-bar.svelte';
import { gridSort } from '$lib/grid/sort-state.svelte';
import { FAVORITES_SOURCE, LOOP_SOURCE, type RowSource } from '$lib/grid/grid.svelte';

const asked = vi.hoisted(() => ({ queries: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/favorites') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets' || path === '/loops') {
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
let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	asked.queries = [];
	at.url = new URL('http://localhost/favorites');
	gridSort.set('newest');
});

async function grid(source?: RowSource) {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(AssetGrid, {
		target: host,
		props: {
			query: {},
			title: 'Files',
			empty: 'Nothing here',
			...(source ? { source } : {})
		}
	});
	flushSync();
	for (let tick = 0; tick < 3; tick += 1) {
		await Promise.resolve();
		flushSync();
	}
}

const menu = () => ordersOffered(screenBar.tools.sorts);
const labelOf = (key: string) => menu().find((one) => one.value === key)?.label;

describe('the Favorites orders', () => {
	it('are on the Favorites menu, in their words', async () => {
		await grid(FAVORITES_SOURCE);

		expect(labelOf('favorited')).toBe('Recently favorited');
		expect(labelOf('favorited_oldest')).toBe('Oldest favorited');
		// The whole vocabulary is still there beside them.
		expect(labelOf('newest')).toBe('Newest first');
		expect(labelOf('favorite')).toBeUndefined();
	});

	it('are asked for from the address, so a link carries the order', async () => {
		at.url = new URL('http://localhost/favorites?sort=favorited_oldest');
		await grid(FAVORITES_SOURCE);

		expect(asked.queries.at(-1)?.sort).toBe('favorited_oldest');
	});

	it('are on no other wall of files, and a wall arrived at holding one asks in its own order', async () => {
		gridSort.set('favorited');
		at.url = new URL('http://localhost/browse');
		await grid();

		expect(labelOf('favorited')).toBeUndefined();
		expect(labelOf('favorited_oldest')).toBeUndefined();
		expect(asked.queries.at(-1)?.sort).toBe('newest');
	});
});

describe('the wall of marks', () => {
	it('names its time orders by when each mark was made', async () => {
		at.url = new URL('http://localhost/loops');
		await grid(LOOP_SOURCE);

		expect(labelOf('newest')).toBe('Recently created');
		expect(labelOf('oldest')).toBe('Oldest created');
		expect(labelOf('name_az')).toBe('Name A-Z');
	});

	it("names its size orders by a mark's length, Longest and Shortest", async () => {
		at.url = new URL('http://localhost/loops');
		await grid(LOOP_SOURCE);

		expect(labelOf('largest')).toBe('Longest');
		expect(labelOf('smallest')).toBe('Shortest');
	});
});
