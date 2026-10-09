// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The header's Add as person, over the whole page and over the whole group. The naming field lives
 * on the bar, which rises over a selection, and a header row arrives with nothing picked: each item
 * has to raise the bar with its faces counted and the picker open. The server's answers are faked.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

vi.mock('$app/state', () => ({
	page: {
		url: new URL('http://localhost/organize/faces-to-name/pile-1'),
		params: { id: 'pile-1' },
		state: {}
	},
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false }
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: () => {}, pushState: () => {} }));
vi.mock('$lib/library/changes.svelte', async () => ({
	...(await vi.importActual<typeof import('$lib/library/changes.svelte')>(
		'$lib/library/changes.svelte'
	)),
	reloadOnLibraryChange: () => {}
}));

const ON_PAGE = ['t1', 't2', 't3'];
const IN_GROUP = [...ON_PAGE, 't4', 't5'];

vi.mock('$lib/api/client', async (importActual) => ({
	...(await importActual<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(async (path: string) => {
			if (path === '/faces/groups/pile-1/tracks') return { track_ids: IN_GROUP };
			if (path.startsWith('/faces/groups/'))
				return {
					group: {
						id: 'pile-1',
						status: 'open',
						size: IN_GROUP.length,
						faces: ON_PAGE.map((id) => ({
							track_id: id,
							asset_id: `a-${id}`,
							started_ms: 0,
							ended_ms: 0
						}))
					},
					total: IN_GROUP.length,
					offset: 0
				};
			if (path.startsWith('/people')) return { items: [], total: 0 };
			return {};
		}),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		del: vi.fn(async () => ({}))
	}
}));

import PileDetail from './PileDetail.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

async function settle() {
	for (let round = 0; round < 10; round += 1) {
		flushSync();
		await tick();
	}
}

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	document.body.innerHTML = '';
});

function row(words: RegExp): HTMLElement | undefined {
	return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((one) =>
		words.test(one.textContent ?? '')
	);
}

async function choose(item: RegExp) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PileDetail, { target: host, props: {} }) as Record<string, unknown>;
	await vi.waitFor(() => expect(host.textContent).toContain('Add as person'), { interval: 1 });
	host
		.querySelector('button[aria-label="More for the faces here"]')
		?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	await settle();
	const naming = row(/Add as person/);
	naming?.focus();
	naming?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
	await settle();
	await vi.waitFor(() => expect(row(item)).toBeDefined(), { interval: 1 });
	row(item)?.click();
	await settle();
}

function count(): string | undefined {
	return document.querySelector('.count[aria-live]')?.textContent?.trim();
}

it('opens the picker over every face on this page', async () => {
	await choose(/All 3 on this page/);
	await vi.waitFor(() => expect(count()).toBe('3 faces selected'), { interval: 1 });
	expect(document.querySelector('input[aria-label="Filter the people list"]')).not.toBeNull();
	// What will be named wears the ring.
	expect(host.querySelectorAll('.face[aria-pressed="true"]')).toHaveLength(3);
});

it('opens the picker over every face in the group, past the page', async () => {
	await choose(/All 5 in this group/);
	await vi.waitFor(() => expect(count()).toBe('5 faces selected'), { interval: 1 });
	expect(document.querySelector('input[aria-label="Filter the people list"]')).not.toBeNull();
});
