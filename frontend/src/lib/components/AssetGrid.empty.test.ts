/*
 * A wall with nothing on it IS the screen, so what it draws is a page's empty state: the glyph,
 * the sentence and the one action that ends the state (Browse hands "Clear the search" when its
 * filters are what emptied it). A bare line of quiet ink between the bar and the foot reads as the
 * page having failed to draw.
 */
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';

import AssetGrid from './AssetGrid.svelte';

const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse?tags=x') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/assets') return { items: [], total: 0, limit: 24, offset: 0, complete: true };
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
		state: {},
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | undefined;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	host?.remove();
	host = undefined;
});

async function settle() {
	for (let round = 0; round < 6; round += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

it("draws an empty wall as a page's empty state, never a loose line", async () => {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: { tags: 'x' }, title: 'Files', empty: 'Nothing here matches.' }
	}) as Record<string, unknown>;
	await settle();
	await settle();

	const empty = host.querySelector<HTMLElement>('.empty[role="status"]');
	expect(empty, 'no page-scope empty state').not.toBeNull();
	expect(empty?.classList.contains('block')).toBe(false);
	expect(empty?.textContent).toContain('Nothing here matches.');
	expect(host.querySelector('p.note')).toBeNull();
	// And no pager saying "No files" under it: the empty state is the whole screen.
	expect(host.textContent).not.toContain('No files');
	// Nor an empty footer holding the pager's height under it.
	expect(host.querySelector('.frame-footer')).toBeNull();
});

it('Browse hands the wall "Clear the search" when its filters are what emptied it', () => {
	// Read from the page rather than mounted: the page is a route with a library, a picker and a
	// scan behind it, and the one fact wanted is which action it hands the wall for a filtered
	// empty. The wall's own drawing of that action is the test above.
	const page = readFileSync('src/routes/browse/+page.svelte', 'utf8');
	// A first folder held behind the benchmark offers nothing (the wait is said, not pressed); past
	// that the order is: unread reads, then a filtered empty clears its search.
	expect(page).toMatch(
		/emptyAction=\{held\s*\?\s*undefined\s*:\s*unread\s*\?\s*scanNow\s*:\s*filtered\s*\?\s*clearSearch/
	);
	expect(page).toContain('{#snippet clearSearch()}');
	expect(page).toContain('Clear the search');
});
