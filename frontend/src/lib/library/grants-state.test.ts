import { afterEach, describe, expect, it, vi } from 'vitest';

import { Grants } from './grants-state.svelte';

/* Handing a folder over, from the page's side.
 *
 * The interesting cases are the two that are not "it worked": a cancelled dialog, which must not
 * draw an error because changing your mind is what Cancel is for, and a refusal from the server,
 * whose own sentence has to reach the screen rather than being replaced with something generic.
 */

const originalFetch = globalThis.fetch;

function serve(handlers: Record<string, () => Response>) {
	const mock = vi.fn(async (url: URL | RequestInfo, init?: RequestInit): Promise<Response> => {
		const path = new URL(String(url), 'http://sift.test').pathname;
		const key = `${init?.method ?? 'GET'} ${path}`;
		const handler = handlers[key] ?? handlers[path];
		if (handler === undefined) throw new Error(`nothing serves ${key}`);
		return handler();
	});
	globalThis.fetch = mock as unknown as typeof fetch;
	return mock;
}

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

afterEach(() => {
	globalThis.fetch = originalFetch;
	delete window.sift;
});

describe('in a browser', () => {
	/* Not a missing feature. The dialog belongs to the machine, and granting a folder is exactly the
	 * decision that should require sitting at it, so the screen says so instead of drawing a button
	 * that cannot work. */
	it('cannot add a folder', () => {
		expect(new Grants().canAdd).toBe(false);
	});

	it('still reads the list, so it can say which folders were handed over', async () => {
		serve({
			'/api/library/grants': () => json({ grants: [{ id: '01H', path: 'D:\\M', granted_at: 1 }] })
		});
		const grants = new Grants();

		await grants.load();

		expect(grants.items).toHaveLength(1);
		expect(grants.failed).toBeNull();
	});

	it('says so when the list cannot be read', async () => {
		serve({ '/api/library/grants': () => json({ detail: 'Nope.' }, 500) });
		const grants = new Grants();

		await grants.load();

		expect(grants.failed).toBe('Nope.');
		expect(grants.loading).toBe(false);
	});
});
