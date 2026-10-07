/*
 * An admin's view of a person draws its header once, with what sits under the cover already in it:
 * those reads are asked beside the row and the page waits for both, so the wall under the header
 * never has its box shortened after it measured it.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { session } from '$lib/shell/session.svelte';

const cover = vi.hoisted(() => {
	let release: (value: unknown) => void = () => {};
	const waiting = new Promise((done) => (release = done));
	return { waiting, release: (value: unknown) => release(value) };
});

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const get = vi.fn((path: string) => {
		if (path === '/people/p1') {
			return Promise.resolve({
				id: 'p1',
				name: 'Anouk Vestergaard',
				asset_count: 3,
				size_bytes: 30
			});
		}
		if (
			/^\/(faces\/identified\/people|people|swap\/people)\/p1\/?(recognition|held-faces)?$/.test(
				path
			)
		) {
			return cover.waiting;
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
	page: { state: {}, params: { id: 'p1' }, url: new URL('http://localhost/people/p1') }
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	session.viewer = undefined;
});

async function turns(): Promise<string> {
	for (let turn = 0; turn < 20; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	return host?.textContent ?? '';
}

it('waits for what sits under the cover before it draws the header', async () => {
	session.viewer = { id: 'u1', username: 'admin', role: 'admin' } as typeof session.viewer;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });

	expect(await turns()).not.toContain('Anouk Vestergaard');

	cover.release({ confirmed: 0, matched: 0, waiting: 0, unnamed_from_folder: 0, items: [] });
	expect(await turns()).toContain('Anouk Vestergaard');
});
