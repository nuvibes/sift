/*
 * A file arriving must not disturb the wall: tiles keep their elements (asserted with `toBe` on the
 * node) and arrivals are counted, not packed in. jsdom's widths are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { arrivals } from '$lib/library/changes.svelte';
import { TILE_ID } from '$lib/components/common';

const page = vi.hoisted(() => ({ items: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets') {
				/* `from` is honoured, or a shifted page would pass every assertion. */
				const asked = options?.query ?? {};
				const anchor = asked.from as string | undefined;
				const offset =
					anchor === undefined
						? Number(asked.offset ?? 0)
						: Math.max(
								0,
								page.items.findIndex((one) => one.id === anchor)
							);
				const limit = Number(asked.limit ?? page.items.length);
				return {
					items: page.items.slice(offset, offset + limit),
					total: page.items.length,
					limit,
					offset,
					complete: true
				};
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

/* `replaceState` too, or unhandled rejections hide behind passing tests. */
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

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

function file(id: string, shape: { width: number; height: number }) {
	return {
		id,
		media_type: 'video',
		duration_ms: 4000,
		thumb: true,
		favorite: false,
		rating: 0,
		concealed: false,
		shared: false,
		...shape
	};
}

/* Mixed proportions: the harder case, and the ordinary one. */
function library(count: number, from = 0) {
	const shapes = [
		{ width: 1920, height: 1080 },
		{ width: 1080, height: 1920 },
		{ width: 1440, height: 1080 }
	];
	return Array.from({ length: count }, (_, index) =>
		file(`a${from + index}`, shapes[(from + index) % shapes.length])
	);
}

beforeEach(() => {
	// A zero-wide wall draws nothing, and every assertion would pass.
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
	page.items = [];
});

async function settle() {
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

async function wall() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here' }
	});
	await settle();
	await settle();
	return host;
}

function tiles(where: HTMLElement): Map<string, Element> {
	const found = new Map<string, Element>();
	for (const element of where.querySelectorAll(`[${TILE_ID}]`)) {
		const id = element.getAttribute(TILE_ID);
		if (id) found.set(id, element);
	}
	return found;
}

describe('a file arriving at the front of the wall', () => {
	it('draws the tiles at all', async () => {
		page.items = library(12);

		const drawn = tiles(await wall());

		expect(drawn.size, 'no tiles were drawn, so nothing below proves anything').toBe(12);
	});

	/* Driven by the press: identity, never appearance. */
	it('leaves every tile that was already there in its own element when the new ones are taken', async () => {
		page.items = library(12);
		const before = tiles(await wall());

		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		const take = [...host.querySelectorAll('button')].find((one) =>
			/\bnew\b/.test(one.textContent ?? '')
		);
		expect(take, 'there was nothing to press, so nothing below is being tested').toBeDefined();
		take?.click();
		await settle();
		await settle();

		const after = tiles(host);
		expect(after.get('new'), 'pressing it did not bring the new file in').toBeDefined();

		// One in pushes one off the end; the rest must be the SAME elements.
		let kept = 0;
		for (const [id, element] of before) {
			const now = after.get(id);
			if (now === undefined) continue;
			kept += 1;
			expect(now, `${id} was destroyed and rebuilt rather than moved`).toBe(element);
		}
		expect(kept, 'almost nothing survived, so identity was never really tested').toBeGreaterThan(8);
	});

	/* The page holds; what arrived above is counted, the count being the positive control. */
	it('leaves every tile exactly where it was', async () => {
		page.items = library(12);
		const before = tiles(await wall());
		const first = before.get('a0') as HTMLElement;
		const was = first.style.transform;

		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		expect(
			was,
			'the tile was never placed, so holding still cannot be told from not drawing'
		).not.toBe('');
		expect(first.style.transform, 'the wall re-laid-out under somebody reading it').toBe(was);
	});

	it('says how many arrived rather than showing them', async () => {
		page.items = library(12);
		const where = await wall();

		expect(where.textContent, 'a count was already showing before anything arrived').not.toMatch(
			/\bnew\b/
		);

		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		expect(
			tiles(host).get('new'),
			'the new file was drawn into the wall after all'
		).toBeUndefined();
		expect(host.textContent, 'nothing offered the file that arrived').toMatch(/1\s*new/);
	});

	/* A catch-up that finds the page changed must not zero the untaken arrivals. */
	it('keeps counting the new ones when something on the page changes too', async () => {
		page.items = library(12);
		await wall();

		const changed = library(12);
		changed[0] = { ...changed[0], favorite: true };
		page.items = [
			file('n1', { width: 1920, height: 1080 }),
			file('n2', { width: 1080, height: 1920 }),
			...changed
		];
		arrivals.changed();
		await settle();
		await settle();

		expect(host.textContent, 'the count reset when the page was rewritten under it').toMatch(
			/2\s*new/
		);
	});
});
