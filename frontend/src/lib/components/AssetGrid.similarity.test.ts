/*
 * SIMILARITY AS AN ORDER on a wall of files: close to the words of a Smart Search, or to the file a
 * wall is like, and dimmed with its reason where there is neither. Asserted on the menu the bar is
 * handed and on the request a page is asked with; the server's half is pinned beside the route.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { ordersOffered, screenBar } from '$lib/components/shell/screen-bar.svelte';
import {
	gridSort,
	SIMILARITY,
	SIMILARITY_NEEDS,
	SIMILAR_TO_FILE,
	SIMILAR_TO_WORDS
} from '$lib/grid/sort-state.svelte';
import { LOOP_SOURCE, type RowSource } from '$lib/grid/grid.svelte';

const asked = vi.hoisted(() => ({ queries: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

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
	at.url = new URL('http://localhost/browse');
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

const row = (key: string) => menu().find((one) => one.value === key);
const last = () => asked.queries.at(-1) ?? {};
const ANCHOR = '01HX0000000000000000000001';

describe('Similarity on a wall with nothing to compare with', () => {
	it('is drawn dimmed, with the reason in words', async () => {
		await grid();

		expect(row(SIMILARITY)).toMatchObject({ disabled: true, note: SIMILARITY_NEEDS });
	});

	it('is never asked for, even when it is the remembered order', async () => {
		gridSort.set(SIMILARITY);
		await grid();

		expect(last().sort).toBe('newest');
		expect(screenBar.tools.sort).toBe('newest');
	});

	it('is dimmed on a search for the words, where Closest match is the order', async () => {
		at.url = new URL('http://localhost/browse?q=red+car');
		await grid();

		expect(row(SIMILARITY)?.disabled).toBe(true);
		expect(row('relevance')?.label).toBe('Closest match');
		expect(last().sort).toBe('relevance');
	});
});

describe('Similarity on a Smart Search', () => {
	it('is the order a Smart Search opens in, close to the words', async () => {
		at.url = new URL('http://localhost/browse?q=red+car&meaning=1');
		await grid();

		expect(row(SIMILARITY)).toMatchObject({ note: SIMILAR_TO_WORDS });
		expect(row(SIMILARITY)?.disabled).toBeUndefined();
		expect(last()).toMatchObject({ sort: SIMILARITY, meaning: '1' });
	});

	it('replaces Closest match, which only ranks the words as words', async () => {
		at.url = new URL('http://localhost/browse?q=red+car&meaning=1');
		await grid();

		expect(row('relevance')).toBeUndefined();
	});

	it('keeps an order somebody chose', async () => {
		gridSort.set('name_az');
		at.url = new URL('http://localhost/browse?q=red+car&meaning=1');
		await grid();

		expect(last().sort).toBe('name_az');
	});
});

describe('Similarity on a wall of files like one file', () => {
	it('is close to the file, and its words stay words', async () => {
		at.url = new URL(`http://localhost/browse?like=${ANCHOR}&q=beach`);
		await grid();

		expect(row(SIMILARITY)).toMatchObject({ note: SIMILAR_TO_FILE });
		expect(last()).toMatchObject({ sort: SIMILARITY, meaning: '0' });
	});

	/* Similar to this > See all writes the file into the words (`q=like:<id>`). There are no words
	   to score there, so Closest match would be an order that changes nothing, and a fifteenth row
	   that would push Random under the list's scroll. */
	it('offers no Closest match when the words are only the file', async () => {
		at.url = new URL(`http://localhost/browse?q=like:${ANCHOR}`);
		await grid();

		expect(row('relevance'), 'Closest match on a wall with no words').toBeUndefined();
		expect(row(SIMILARITY)).toMatchObject({ note: SIMILAR_TO_FILE });
		expect(last().sort).toBe(SIMILARITY);
	});
});

describe('a wall that does not order files by closeness', () => {
	it('does not offer it', async () => {
		at.url = new URL('http://localhost/loops');
		await grid(LOOP_SOURCE);

		expect(row(SIMILARITY)).toBeUndefined();
	});
});
