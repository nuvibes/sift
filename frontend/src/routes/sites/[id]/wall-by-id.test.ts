/*
 * A Site's page asks for ITS files: the wall names the Site by id, so another Site spelled the same
 * way never adds its files to this page's count.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const asked = vi.hoisted(() => [] as { path: string; query?: Record<string, unknown> }[]);

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const get = vi.fn((path: string, options?: { query?: Record<string, unknown> }) => {
		asked.push({ path, query: options?.query });
		if (path === '/sites/s1') {
			return Promise.resolve({
				id: 's1',
				name: 'Harbor',
				asset_count: 3,
				people_count: 1,
				size_bytes: 30
			});
		}
		return new Promise(() => {});
	});
	const unanswered = vi.fn(() => new Promise(() => {}));
	return { ...real, api: { get, post: unanswered, put: unanswered, del: unanswered } };
});

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn(),
	pushState: vi.fn(),
	afterNavigate: vi.fn(),
	beforeNavigate: vi.fn()
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { state: {}, params: { id: 's1' }, url: new URL('http://localhost/sites/s1') }
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

it('asks for its files by the Site id, never by its name', async () => {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	for (let turn = 0; turn < 20; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	const walls = asked.filter((one) => one.path === '/assets');
	expect(walls.length).toBeGreaterThan(0);
	for (const one of walls) expect(one.query?.sites).toBe('s1');
});
