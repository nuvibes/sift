import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

/* What stays in reach: a chip pressed from the keyboard keeps it (so the address here is a live
   one), and a screen's title row stays clear of the open panel. */

const held = vi.hoisted(() => ({ gone: [] as string[] }));

vi.mock('$app/state', async () => {
	const { SvelteURL } = await import('svelte/reactivity');
	const url = new SvelteURL('http://x/browse');
	return { page: { url } };
});
vi.mock('$app/navigation', () => ({
	goto: vi.fn((where: string) => {
		held.gone.push(where);
		return Promise.resolve();
	})
}));
vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => ({ items: [], text: '', clauses: [], terms: {}, problems: [] })),
		post: vi.fn(async () => ({}))
	},
	ApiError: class extends Error {}
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));

const FilterBar = (await import('./FilterBar.svelte')).default;
const { page } = await import('$app/state');
const { screenBar, FILTERS_PANEL } = await import('./screen-bar.svelte');
const { keepClear } = await import('./kept-clear.svelte');

let drawn: Record<string, unknown> | undefined;
const owner = Symbol('a screen');

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	document.body.replaceChildren();
	screenBar.close();
	vi.unstubAllGlobals();
	held.gone.length = 0;
});

it.each([
	['a presence chip, Has for No', '?tags=any&media=video', 'tags: No tags'],
	["a tag's own chip, refused", '?tags=runway&media=video', 'tags: not runway']
])('keeps the keyboard on %s', async (_name, at, then) => {
	page.url.href = `http://x/browse${at}`;
	screenBar.publish(owner, { filterable: true });
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
	flushSync();

	const chip = host.querySelector<HTMLElement>('.filters .chip .body');
	chip?.focus();
	chip?.click();
	page.url.href = `http://x${held.gone[0]}`;
	flushSync();

	expect(host.querySelector('.filters .chip .body')).toBe(chip);
	expect(document.activeElement).toBe(chip);
	expect(
		(chip?.closest('.chip')?.textContent ?? '')
			.replace(/[^\x20-\x7E]/g, '')
			.replace(/\s+/g, ' ')
			.trim()
	).toBe(then);
});

it('hangs the open panel below the title row a screen keeps clear', () => {
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			disconnect() {}
		}
	);
	page.url.href = 'http://x/tags/t1';
	screenBar.publish(owner, { filterable: true });
	const title = document.createElement('div');
	title.getBoundingClientRect = () => ({ bottom: 150 }) as DOMRect;
	const forget = keepClear(title);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FilterBar, { target: host }) as Record<string, unknown>;
	screenBar.show(FILTERS_PANEL);
	flushSync();

	const row = host.querySelector<HTMLElement>('.bar-row');
	expect(row?.style.getPropertyValue('--panel-clear')).toBe('150px');
	forget();
});
