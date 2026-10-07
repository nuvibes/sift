/*
 * PRESSING RANDOM, which is the one order that means something even when it is already chosen.
 *
 * A shuffle is a draw, so asking for one is asking for a NEW one, and the arrangement has to
 * survive being paged, being come back to, and being sent to somebody else. Neither half is free:
 * the seed has to be minted on the press and it has to live in the address, because nothing the
 * browser remembers travels with a link and nothing in a link survives a fresh draw per request.
 *
 * Asserted on what is SENT and on what is written into the address, which is the whole of this
 * behaviour and the only part a test without a browser can see. That the same seed is the same
 * order is a property of the statement and is pinned where the statement lives.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { ordersOffered, screenBar } from '$lib/components/shell/screen-bar.svelte';
import { gridSort } from '$lib/grid/sort-state.svelte';
import { goto } from '$app/navigation';

const asked = vi.hoisted(() => ({ queries: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));
const went = vi.hoisted(() => ({ to: [] as URL[] }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets') {
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

/* `goto` is where the address change lands, so it is recorded rather than merely swallowed, and
   the grid writes the tile it is on with `replaceState` as tiles arrive, which has to exist or every
   scroll rejects. */
vi.mock('$app/navigation', () => ({
	goto: vi.fn((url: URL) => {
		went.to.push(url);
		at.url = new URL(url);
	}),
	replaceState: vi.fn()
}));
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
	/* Torn down, not merely taken out of the page: a grid left mounted keeps reacting to the
	   shared order store, and asks for pages under the next test's seeds. */
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	asked.queries = [];
	went.to = [];
	at.url = new URL('http://localhost/browse');
	gridSort.set('newest');
});

/** Draw the grid the way Browse does, and let its start-up settle. */
async function grid() {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here' }
	});
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

/** The order menu, as the bar above draws it, and the press it delivers. A press lands the
    address first and writes the remembered order after it, so it settles over a few ticks. */
async function press(order: string) {
	screenBar.tools.onSort?.(order);
	flushSync();
	for (let tick = 0; tick < 3; tick += 1) {
		await Promise.resolve();
		flushSync();
	}
}

function seedIn(url: URL): string | null {
	return url.searchParams.get('seed');
}

describe('pressing Random', () => {
	it('mints a seed and writes it into the address beside the order', async () => {
		await grid();

		await press('random');

		expect(went.to.length, 'the press changed nothing about the address').toBe(1);
		expect(went.to[0].searchParams.get('sort')).toBe('random');
		const seed = seedIn(went.to[0]);
		expect(seed, 'no shuffle was named, so paging would re-draw it').not.toBeNull();
		expect(Number(seed)).toBeGreaterThanOrEqual(0);
		expect(Number(seed)).toBeLessThan(2147483647);
		expect(Number.isInteger(Number(seed))).toBe(true);
	});

	it('mints a DIFFERENT one on the next press, which is what a reshuffle is', async () => {
		await grid();

		await press('random');
		await press('reshuffle');

		expect(went.to.length).toBe(2);
		expect(went.to[1].searchParams.get('sort')).toBe('random');
		/* The whole point of the second row. The chooser refuses to deliver a press of the order
		   already showing, so without it the only way to be shuffled again would be to leave Random
		   and come back. */
		expect(seedIn(went.to[1])).not.toBe(seedIn(went.to[0]));
	});

	it('goes on minting, press after press', async () => {
		/* Three deep, because two can pass on a control that merely alternates, and working once
		   and then not at all is what a row read as an order rather than a press would do (see
		   `action` in `common/Select.svelte`). */
		await grid();

		await press('random');
		await press('reshuffle');
		await press('reshuffle');

		expect(went.to.length).toBe(3);
		const seeds = went.to.map(seedIn);
		expect(new Set(seeds).size, 'two presses handed back the same arrangement').toBe(3);
		for (const url of went.to) expect(url.searchParams.get('sort')).toBe('random');
	});

	it('asks for no page without the seed while the address is still on its way', async () => {
		await grid();
		/* The real `goto` lands the address a moment later, which is a gap a press can fall into:
		   the remembered order changes immediately, and the grid must not ask for a page under
		   Random with no seed before the seeded one. */
		vi.mocked(goto).mockImplementationOnce(async (url: string | URL) => {
			await Promise.resolve();
			went.to.push(new URL(url));
			at.url = new URL(url);
		});
		const before = asked.queries.length;

		await press('random');

		const sent = asked.queries.slice(before);
		expect(sent.length, 'one press asked for more than one page').toBe(1);
		expect(sent[0].sort).toBe('random');
		expect(sent[0].seed, 'a page was asked for under Random with no seed').toBe(seedIn(went.to[0]));
	});

	it('sends the seed with every page it asks for', async () => {
		await grid();

		await press('random');
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		const sent = asked.queries.at(-1) ?? {};
		expect(sent.sort).toBe('random');
		expect(sent.seed).toBe(seedIn(went.to[0]));
	});

	it('offers the reshuffle row only while the grid is actually shuffled', async () => {
		await grid();

		expect(ordersOffered(screenBar.tools.sorts).map((one) => one.value)).not.toContain('reshuffle');

		await press('random');
		await Promise.resolve();
		flushSync();

		const offered = ordersOffered(screenBar.tools.sorts).map((one) => one.value);
		expect(offered).toContain('reshuffle');
		// It is not an order and nothing may remember it as one.
		expect(gridSort.value).toBe('random');
	});

	it('takes the shuffle out of the address when another order is chosen', async () => {
		await grid();

		await press('random');
		await press('oldest');

		const last = went.to.at(-1)!;
		expect(last.searchParams.has('sort'), 'the address still outranks the remembered order').toBe(
			false
		);
		/* A seed left behind describes an arrangement nobody is looking at, in something people copy
		   and send. The server ignores it, so this is about the address rather than the page. */
		expect(last.searchParams.has('seed')).toBe(false);
		expect(gridSort.value).toBe('oldest');
	});
});
