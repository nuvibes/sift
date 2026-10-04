/*
 * A collection the server says is not there draws the page saying so, and nothing to act on.
 *
 * A 404 is the one answer that is a fact about the library ("no such id" and "not yours" alike);
 * every other failure is Sift's own and keeps the page with the problem said on it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const server = vi.hoisted(() => {
	class ApiError extends Error {
		constructor(
			readonly status: number,
			readonly detail: string | null
		) {
			super(detail ?? 'refused');
		}
	}
	return { ApiError, status: 404 };
});

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/api/client')>();
	const answer = vi.fn(async (path: string) => {
		if (path.startsWith('/collections/nope'))
			throw new server.ApiError(server.status, 'Not found.');
		return { items: [], total: 0 };
	});
	return {
		...real,
		ApiError: server.ApiError,
		isMissing: (error: unknown) => error instanceof server.ApiError && error.status === 404,
		api: { get: answer, post: answer, put: answer, del: answer }
	};
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
	page: {
		state: {},
		params: { id: 'nope' },
		url: new URL('http://localhost/collections/nope')
	}
}));

const Page = (await import('./+page.svelte')).default;

let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
});

async function open(): Promise<string> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Page, { target: host });
	for (let turn = 0; turn < 10; turn += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
	return (host.textContent ?? '').replace(/\s+/g, ' ');
}

describe('a collection that is not there', () => {
	it('says so, with no header, tabs or verbs', async () => {
		server.status = 404;
		const said = await open();
		expect(said).toContain("That collection isn't here");
		expect(host!.querySelector('[role="tablist"]')).toBeNull();
		expect(said).not.toContain('0 items');
	});

	it('keeps the page and says it could not be loaded for any other failure', async () => {
		server.status = 500;
		const said = await open();
		expect(said).not.toContain("That collection isn't here");
		expect(said).toContain("This collection couldn't be loaded.");
	});
});
