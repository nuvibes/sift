// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Files wall of the files a product gave up on says, under each tile, WHY.
 *
 * The Importing pane's "24 files couldn't have thumbnails generated and are left out" opens
 * `/browse?left_out=thumbnails`, which answers which 24; this is the half that answers why. The
 * server sends the why in plain words on each row (`left_out`); the wall draws it on the line a
 * mark's name is drawn on, part of the row's height, so the wall still pages by whole rows. Every
 * other wall of files has no line at all.
 *
 * The layout is arithmetic over the container's width, and jsdom reports every width as zero, so
 * the width and height are stubbed, as the Loops wall's own caption test stubs them.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { CAPTION_HEIGHT } from '$lib/grid/justify';
import AssetGrid from './AssetGrid.svelte';

const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

function file(index: number, why: string | null) {
	return {
		id: `file${index}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 4000,
		original_filename: `clip${index}.mp4`,
		thumb: false,
		preview: false,
		verdict: null,
		left_out: why,
		art: null,
		favorite: false,
		pinned: false,
		rating: null,
		o_count: 0,
		views: 0,
		resume_ms: null,
		concealed: false,
		hidden: false,
		hidden_here: false,
		shared: false,
		shared_here: false,
		restricted: false,
		restricted_here: false,
		unreachable: false
	};
}

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path !== '/assets') return { items: [], total: 0, limit: 50, offset: 0, complete: true };
			const asked = options?.query ?? {};
			const items = asked.left_out
				? [file(1, "It wouldn't open."), file(2, "There's no video in it.")]
				: [file(1, null), file(2, null)];
			return { items, total: items.length, limit: 50, offset: 0, complete: true };
		}),
		post: vi.fn(async () => undefined),
		put: vi.fn(async () => undefined),
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
let mounted: Record<string, unknown> | null = null;

beforeEach(() => {
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => 900
	});
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
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

async function wall(query: Record<string, string>): Promise<HTMLElement> {
	at.url = new URL(`http://localhost/browse?${new URLSearchParams(query)}`);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query, title: 'Browse', empty: 'Nothing here matches.' }
	}) as Record<string, unknown>;
	await settle();
	await settle();
	return host;
}

it('draws why each file was left out on the line under its tile', async () => {
	const screen = await wall({ left_out: 'thumbnails' });
	const lines = [...screen.querySelectorAll<HTMLElement>('.placed .caption')];
	expect(lines.length, 'the wall drew no tiles, so nothing was tested').toBe(2);
	expect(lines.map((one) => one.textContent?.trim())).toEqual([
		"It wouldn't open.",
		"There's no video in it."
	]);
	expect(lines[0].style.height).toBe(`${CAPTION_HEIGHT}px`);
});

it('offers the whole reason on hover, since the line is cut at one line', async () => {
	const screen = await wall({ left_out: 'thumbnails' });
	const line = screen.querySelector<HTMLElement>('.placed .caption');
	const wrap = line?.querySelector<HTMLElement>('.wrap');
	expect(wrap, 'the line has no label to hover').not.toBeNull();
	wrap?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('[role="tooltip"]')) throw new Error('no label yet');
	});
	expect(document.querySelector('[role="tooltip"]')?.textContent).toBe("It wouldn't open.");
});

it('draws no line under the tiles of any other wall of files', async () => {
	const screen = await wall({ tags: 'beach' });
	expect(screen.querySelectorAll('.placed').length, 'the wall drew no tiles').toBe(2);
	expect(screen.querySelector('.placed .caption')).toBeNull();
});
